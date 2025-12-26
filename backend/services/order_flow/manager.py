# backend/services/order_flow/manager.py
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ...models import OrderFlowSyncState
from .fetcher import OrderFlowFetcher
from .processor import OrderFlowProcessor
from .storage import OrderFlowService

logger = logging.getLogger("OrderFlowManager")

# 自动同步区域
AUTO_AREAS = ["SE3"]
INITIAL_START_DATE = "2025-01-01T00:00:00"

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
        if curr >= archive_limit:
            return

        logger.info(f"[{area}] 📚 启动历史归档 (API A): {curr} -> {archive_limit}")
        
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
                contract_resp = self.fetcher.fetch_contract_list(area, day_start, day_end)
                contracts = contract_resp.get('contracts', [])
                
                if contracts:
                    logger.info(f"[{area}] {target_date_str} 共有 {len(contracts)} 个合约需归档")
                    
                    for c in contracts:
                        cid = c.get('contractId') or c.get('id')
                        
                        # 2. 抓取该合约的完整历史 (含 Snapshots)
                        # API: OrderBook/ByContractId
                        book_data = self.fetcher.fetch_historical_book(cid)
                        
                        # 3. 构造元数据 (API A 可能不返回 delivery area，需手动补全)
                        meta = {
                            'contract_name': c.get('contractName'),
                            'delivery_start': c.get('deliveryStart'),
                            'delivery_end': c.get('deliveryEnd'),
                            'delivery_area': area
                        }
                        
                        # 4. 解析并入库 (重点是 Snapshots)
                        snapshots = self.processor.process_historical_revisions_response(book_data, meta)
                        if snapshots:
                            self.storage.save_snapshots(snapshots)
                            
                # 成功推进一天
                self._update_state(state, last_archived_time=day_end, status="running")
                curr = day_end
                
            except Exception as e:
                logger.error(f"[{area}] 历史归档失败 ({target_date_str}): {e}")
                self._update_state(state, status="error", last_error=str(e))
                return

    def sync_realtime_stream(self, area: str, return_ticks: bool = False):
        """
        【实时同步】
        策略：使用 OrderRevisions/ByUpdatedTime 追赶最新 Ticks
        """
        state = self._get_or_create_state(area)
        now = datetime.utcnow()
        
        # 异常状态重置逻辑
        safe_start_limit = now - timedelta(hours=48)
        start_time = state.last_realtime_time
        
        if start_time < safe_start_limit:
            start_time = safe_start_limit
            logger.warning(f"[{area}] 实时进度落后太久，重置为 48 小时前")
        elif start_time > now:
            start_time = now - timedelta(hours=2)
            logger.warning(f"[{area}] 实时进度异常(超前)，重置为 2 小时前")

        if start_time >= now - timedelta(minutes=1):
            return [] if return_ticks else None 

        logger.info(f"[{area}] 🚀 启动实时同步 (API B): {start_time} -> {now}")
        
        all_new_ticks = []
        try:
            total_saved = 0
            # 使用流式 Fetcher 消费 API B
            for chunk in self.fetcher.fetch_recent_orders(area, start_time, now):
                ticks = self.processor.process_recent_orders_response(chunk)
                if ticks:
                    self.storage.save_ticks(ticks)
                    total_saved += len(ticks)
                
                if return_ticks:
                    all_new_ticks.extend(ticks)
            
            self._update_state(state, last_realtime_time=now, status="ok")
            if total_saved > 0:
                logger.info(f"[{area}] 实时同步完成，入库 {total_saved} 条 Ticks")

            return all_new_ticks if return_ticks else None
            
        except Exception as e:
            logger.error(f"[{area}] 实时同步失败: {e}")
            self._update_state(state, status="warning", last_error=str(e))
            return [] if return_ticks else None

    def sync_all(self):
        for area in AUTO_AREAS:
            try:
                self.sync_history_backfill(area)
                self.sync_realtime_stream(area)
            except Exception as e:
                logger.error(f"[{area}] Manager Loop Error: {e}")