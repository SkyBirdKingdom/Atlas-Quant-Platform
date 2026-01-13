# backend/services/order_flow/manager.py
import logging
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session
from ...models import OrderFlowSyncState, OrderContract
from .fetcher import OrderFlowFetcher
from .processor import OrderFlowProcessor
from .storage import OrderFlowService
from ...database import SessionLocal
import gc

logger = logging.getLogger("OrderFlowManager")

# 订单初始回溯时间 (仅当数据库无记录时使用，通常由 Manager 内部逻辑处理，这里作为备注或传递参数)
INITIAL_START_DATE = "2025-10-01T00:00:00"
class OrderFlowManager:
    def __init__(self, db: Session):
        self.db = db
        self.fetcher = OrderFlowFetcher()
        self.processor = OrderFlowProcessor()
        self.storage = OrderFlowService(db)
    
    def _get_or_create_state(self, area: str) -> OrderFlowSyncState:
        state = self.db.query(OrderFlowSyncState).filter(OrderFlowSyncState.area == area).first()
        if not state:
            start_time = datetime.fromisoformat(INITIAL_START_DATE)
            # 实时指针默认回溯1小时
            realtime_start = datetime.now(timezone.utc) - timedelta(hours=1)
            
            state = OrderFlowSyncState(
                area=area, 
                last_archived_time=start_time,
                last_realtime_time=realtime_start 
            )
            self.db.add(state)
            self.db.commit()
            logger.info(f"[{area}] 初始化同步状态: 默认回溯1小时")
        return state
    
    def _update_state(self, state, **kwargs):
        for k, v in kwargs.items():
            if isinstance(v, datetime) and v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            setattr(state, k, v)
        state.updated_at = datetime.now(timezone.utc)
        self.db.commit()

    # --- 核心状态管理 ---
    def _update_checkpoint(self, area: str, new_time: datetime):
        """更新断点时间"""
        state = self.db.query(OrderFlowSyncState).filter_by(area=area).first()
        if not state:
            state = OrderFlowSyncState(area=area, last_realtime_time=new_time)
            self.db.add(state)
        else:
            # 只有当新时间大于旧时间时才更新，防止回滚
            if new_time > state.last_realtime_time:
                state.last_realtime_time = new_time
        self.db.commit()
    
    def _process_single_contract(self, area: str, date_str: str, contract_info: dict, is_cold: bool):
        """
        [线程任务] 处理单个合约：下载 -> 解析 -> 存储 (DB 或 Parquet)
        """
        cid = contract_info.get('contract_id')

        # 每个线程使用独立的 DB Session (仅当需要写入 DB 时)
        thread_db = SessionLocal()
        thread_storage = OrderFlowService(thread_db)
        
        try:
            # 1. 下载全量历史
            book_data = self.fetcher.fetch_historical_revisions(area, cid, date_str)
            
            ticks = self.processor.process_historical_revisions_response(book_data)

            del book_data
            book_data = None
            
            if ticks:
                # 3. 存储 (冷热分离)
                if is_cold:
                    # [Cold] 存为 Parquet 文件 (不占用 DB 连接)
                    thread_storage.save_ticks_to_parquet(ticks, area, date_str, cid)
                else:
                    # [Hot] 存入 PostgreSQL
                    thread_storage.save_ticks(ticks)
            
            # 4. 【关键】标记该合约已完成
            thread_storage.mark_contract_archived(cid)
            count = len(ticks) if ticks else 0
            return True, count

        except Exception as e:
            logger.error(f"合约 {cid} 处理失败: {e}")
            return False, 0
        finally:
            thread_db.close()
            del ticks
            del thread_storage
            del thread_db
            gc.collect()
    
    def sync_history_backfill(self, area: str):
        """
        【并发版 T+1 历史归档】
        1. 并发处理：使用 ThreadPoolExecutor
        2. 冷热分离：>7天存文件，<=7天存数据库
        """
        state = self._get_or_create_state(area)
        
        # 归档结束点：48小时前 (确保数据就绪)
        archive_limit = datetime.now(timezone.utc) - timedelta(hours=48)
        
        # 冷热分界线：7天前
        hot_cold_threshold = datetime.now(timezone.utc) - timedelta(days=7)

        last_archived = state.last_archived_time
        if last_archived and last_archived.tzinfo is None:
            last_archived = last_archived.replace(tzinfo=timezone.utc)

        # 对齐时间到 00:00:00
        curr = last_archived or (datetime.now(timezone.utc) - timedelta(days=8))
        curr = curr.replace(hour=0, minute=0, second=0, microsecond=0)

        if curr >= archive_limit:
            return

        logger.info(f"[{area}] 📚 启动高并发归档: {curr.date()} -> {archive_limit.date()}")
        
        # 建议线程数：CPU核心数 * 2 或 4，或者固定 10 (网络IO密集型)
        MAX_WORKERS = 10 

        try:
            while curr < archive_limit:
                target_date_str = curr.strftime('%Y-%m-%d')
                
                # 判断冷热 (比较 delivery start date 与 当前时间)
                # curr 代表 delivery date
                is_cold = curr < hot_cold_threshold
                mode_str = "❄️ COLD (Parquet)" if is_cold else "🔥 HOT (DB)"
                logger.info(f"[{area}] 处理日期 {target_date_str} 模式: {mode_str}")

                # 1. 获取合约列表 (主线程执行)
                try:
                    contract_resp = self.fetcher.fetch_contract_list(area, target_date_str)
                    
                    # 顺便保存合约元数据 (存 DB)
                    contracts_meta = self.processor.process_contracts_response(contract_resp)
                    self.storage.save_contracts(contracts_meta)
                    logger.info(f"[{area}] {target_date_str} 获取合约列表，共 {len(contracts_meta)} 个合约")
                    
                except Exception as e:
                    logger.error(f"[{area}] 获取合约列表失败 ({target_date_str}): {e}")
                    break
                
                # --- Step 2: 查询剩余任务 (Filter) ---
                # 从 DB 查出【当天】且【未归档】的合约
                pending_contracts = self.db.query(OrderContract).filter(
                    OrderContract.delivery_area == area,
                    OrderContract.delivery_date_utc == curr.date(),
                    OrderContract.is_archived == False
                ).all()

                # 将 SQLAlchemy 对象转为 Dict，方便传入线程
                pending_list = [
                    {"contract_id": c.contract_id, "contract_name": c.contract_name} 
                    for c in pending_contracts
                ]
                
                total_pending = len(pending_list)

                if total_pending == 0:
                    logger.info(f"[{area}] {target_date_str} 所有合约已归档，推进日期。")
                    curr += timedelta(days=1)
                    self._update_state(state, last_archived_time=curr)
                    continue
                
                logger.info(f"[{area}] {target_date_str} 剩余 {total_pending} 个合约待处理 ({'Cold' if is_cold else 'Hot'})")

                # --- Step 3: 并发执行 (Execute) ---
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    future_to_cid = {
                        executor.submit(
                            self._process_single_contract, 
                            area, target_date_str, c_info, is_cold
                        ): c_info['contract_id'] for c_info in pending_list
                    }
                    
                    completed_in_batch = 0
                    
                    for future in as_completed(future_to_cid):
                        cid = future_to_cid[future]
                        try:
                            success, count = future.result()
                            if success:
                                completed_in_batch += 1
                        except Exception as exc:
                            logger.error(f"任务异常 {cid}: {exc}")
                
                # --- Step 4: 检查是否全部完成 (Check) ---
                # 再次查询 DB，看是否还有剩余
                remaining = self.db.query(OrderContract).filter(
                    OrderContract.delivery_area == area,
                    OrderContract.delivery_date_utc == curr.date(),
                    OrderContract.is_archived == False
                ).count()

                if remaining == 0:
                    logger.info(f"[{area}] ✅ {target_date_str} 完成 (本批 {completed_in_batch})")
                    # 只有当天全部搞定，才推进全局指针
                    curr += timedelta(days=1)
                    self._update_state(state, last_archived_time=curr)
                else:
                    logger.warning(f"[{area}] ⚠️ {target_date_str} 仍有 {remaining} 个失败/未完成，暂不推进日期，下次重试。")
                    # 如果有失败，跳出本次大循环，或者 sleep 一下再试
                    # 这里选择 break，等待下一次 Scheduler 调度再重试，避免死循环轰炸 API
                    break

                
            logger.info(f"[{area}] ✅ 历史归档全部完成")

        except Exception as e:
            logger.error(f"[{area}] 归档主流程异常: {e}")

    # --- 自动同步逻辑 ---
    def sync_realtime(self, area: str):
        """
        【断点续传】实时同步任务
        """
        # 1. 获取断点
        state = self._get_or_create_state(area)
        last_time = state.last_realtime_time
        # 为了防止毫秒级的时间边界丢失，向前回溯 1 分钟（Overlap）
        # 依赖 Storage 层的去重机制处理重复数据
        fetch_start = last_time - timedelta(minutes=1)
        fetch_end = datetime.utcnow()

        logger.info(f"[{area}] 启动增量同步: {fetch_start} -> {fetch_end}")

        try:
            count = 0
            # 2. 调用 Fetcher (流式获取，自动处理 4小时限制)
            # API: updatedTimeFrom - updatedTimeTo [cite: 35, 60-61]
            for chunk_data in self.fetcher.fetch_recent_orders(area, fetch_start, fetch_end):
                # 3. 处理数据
                ticks = self.processor.process_api_response(chunk_data, source_type="Stream")
                
                # 4. 幂等入库 (Storage 层处理去重)
                if ticks:
                    self.storage.save_ticks(ticks)
                    count += len(ticks)
                del ticks
                gc.collect()
            
            # 5. 更新断点 (只有成功才更新)
            self._update_checkpoint(area, fetch_end)
            logger.info(f"[{area}] 同步完成，入库 {count} 条 Ticks，断点更新至 {fetch_end}")

        except Exception as e:
            logger.error(f"[{area}] 同步中断: {e}")
            # 发生异常时不更新 checkpoint，下次任务会自动重试这段时间

    # --- 手动补录逻辑 ---
    def manual_backfill_range(self, area: str, start_str: str, end_str: str):
        """
        【手动补录】强制抓取指定时间段
        场景：发现某天数据缺失，或需要补充历史数据
        """
        start = datetime.fromisoformat(start_str)
        end = datetime.fromisoformat(end_str)
        
        logger.info(f"[{area}] ⚠️ 开始手动补录: {start} -> {end}")
        
        try:
            total = 0
            # 复用 Fetcher 的逻辑，它已经处理了分页和切片
            for chunk_data in self.fetcher.fetch_recent_orders(area, start, end):
                ticks = self.processor.process_api_response(chunk_data, source_type="ManualBackfill")
                self.storage.save_ticks(ticks)
                total += len(ticks)
                
            logger.info(f"[{area}] 手动补录完成，共恢复 {total} 条数据")
        except Exception as e:
            logger.error(f"[{area}] 手动补录失败: {e}")
            raise