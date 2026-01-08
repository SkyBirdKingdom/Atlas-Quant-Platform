# backend/services/order_flow/manager.py
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from ...models import OrderFlowSyncState
from .fetcher import OrderFlowFetcher
from .processor import OrderFlowProcessor
from .storage import OrderFlowService

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
            realtime_start = datetime.utcnow() - timedelta(hours=1)
            
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
            setattr(state, k, v)
        state.updated_at = datetime.utcnow()
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
    
    def sync_history_backfill(self, area: str):
        """
        【历史归档】
        策略：由于 OrderBook/ByContractId 有 14-38 小时延迟，
        我们只归档 '48小时前' 的数据，确保数据已就绪。
        """
        state = self._get_or_create_state(area)
        
        # 归档线：设置为 48 小时前 (避开 38h 的最大延迟)
        archive_limit = datetime.utcnow() - timedelta(hours=48)
        
        curr = state.last_archived_time
        curr = curr.replace(hour=0, minute=0, second=0, microsecond=0)
        if curr >= archive_limit:
            return

        logger.info(f"[{area}] 📚 启动历史归档 (API A): {curr.date()} -> {archive_limit.date()}")
        
        while curr < archive_limit:
            # 每次处理一天
            day_start = curr
            day_end = curr + timedelta(days=1)
            
            # 为了防止死循环，若 day_end 超过 limit 则截断
            if day_end > archive_limit:
                break
            
            target_date_str = day_start.strftime('%Y-%m-%d')
            
            try:
                # 1. 获取该日期的合约列表
                # API: OrderBook/ContractsIds/ByArea
                contract_resp = self.fetcher.fetch_contract_list(area, target_date_str)
                contracts = contract_resp.get('contracts', [])
                
                if contracts:
                    logger.info(f"[{area}] {target_date_str} 共有 {len(contracts)} 个合约需归档")
                    
                    for c in contracts:
                        cid = c.get('contractId') or c.get('id')
                        
                        # 2. 抓取该合约的完整历史 (含 Snapshots)
                        # API: OrderBook/ByContractId
                        book_data = self.fetcher.fetch_historical_revisions(area, cid, target_date_str)
                        
                        # 3. 构造元数据 (API A 可能不返回 delivery area，需手动补全)
                        meta = {
                            'contract_name': c.get('contractName'),
                            'delivery_start': c.get('deliveryStart'),
                            'delivery_end': c.get('deliveryEnd'),
                            'delivery_area': area
                        }
                        
                        # 4. 解析并入库 (重点是 Snapshots)
                        result = self.processor.process_historical_revisions_response(book_data, meta)
                        snapshots = result.get('snapshots', [])
                        if snapshots:
                            self.storage.save_snapshots(snapshots)
                        
                        # 2. 保存 Ticks (这就是你缺失的数据!)
                        ticks = result.get('ticks', [])
                        if ticks:
                            self.storage.save_ticks(ticks)
                            
                # 成功推进一天
                self._update_state(state, last_archived_time=day_end, status="running")
                curr = day_end
                
            except Exception as e:
                logger.error(f"[{area}] 历史归档失败 ({target_date_str}): {e}")
                self._update_state(state, status="error", last_error=str(e))
                return

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