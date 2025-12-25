# backend/services/order_flow/storage.py
import logging
from sqlalchemy.orm import Session
from typing import List
from ...models import OrderFlowTick, OrderBookSnapshot

logger = logging.getLogger("OrderFlowStorage")

class OrderFlowService:
    """
    订单流数据持久化服务
    负责数据库的读写操作
    """
    
    def __init__(self, db: Session):
        self.db = db

    def save_ticks(self, ticks: List[OrderFlowTick]):
        """
        批量保存 Tick 数据
        """
        if not ticks:
            return
        
        try:
            logger.info(f"正在批量写入 {len(ticks)} 条 Tick 数据...")
            # bulk_save_objects 比 add_all 快得多，适合高频数据
            self.db.bulk_save_objects(ticks)
            self.db.commit()
            logger.info("✅ Tick 数据写入成功")
        except Exception as e:
            logger.error(f"❌ Tick 数据写入失败: {e}")
            self.db.rollback()
            raise

    def save_snapshots(self, snapshots: List[OrderBookSnapshot]):
        """
        批量保存盘口快照
        """
        if not snapshots:
            return
            
        try:
            logger.info(f"正在写入 {len(snapshots)} 个盘口快照...")
            self.db.bulk_save_objects(snapshots)
            self.db.commit()
            logger.info("✅ 快照写入成功")
        except Exception as e:
            logger.error(f"❌ 快照写入失败: {e}")
            self.db.rollback()
            raise
            
    def get_latest_tick_time(self, contract_id: str):
        """
        获取某个合约数据库中最新的 Tick 时间
        用于断点续传
        """
        # TODO: Phase 2 实现，用于优化 Fetcher 的查询时间窗口
        pass