# backend/services/order_flow/storage.py
import logging
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from typing import List
from ...models import OrderFlowTick, OrderBookSnapshot

logger = logging.getLogger("OrderFlowStorage")

class OrderFlowService:
    
    def __init__(self, db: Session):
        self.db = db

    def save_ticks(self, ticks: List[OrderFlowTick]):
        """
        批量保存 Tick 数据 (幂等写入)
        遇到 ID 冲突自动跳过 (DO NOTHING)
        """
        if not ticks: return
        
        try:
            # 将对象转换为字典列表
            data_list = []
            for t in ticks:
                data_list.append({
                    "tick_id": t.tick_id,
                    "contract_id": t.contract_id,
                    "contract_name": t.contract_name,
                    "delivery_area": t.delivery_area,
                    "delivery_start": t.delivery_start,
                    "delivery_end": t.delivery_end,
                    "timestamp": t.timestamp,
                    "price": t.price,
                    "volume": t.volume,
                    "remaining_volume": t.remaining_volume,
                    "side": t.side,
                    "type": t.type,
                    "raw_action": t.raw_action,
                    "aggressor_side": t.aggressor_side,
                    "order_id": t.order_id,
                    "revision_number": t.revision_number
                })
            
            # 使用 PostgreSQL 的 ON CONFLICT DO NOTHING
            stmt = insert(OrderFlowTick).values(data_list)
            stmt = stmt.on_conflict_do_nothing(index_elements=['tick_id'])
            
            self.db.execute(stmt)
            self.db.commit()
            
            # logger.debug(f"成功合并写入 {len(ticks)} 条 Tick 数据")
            
        except Exception as e:
            logger.error(f"Tick 数据写入失败: {e}")
            self.db.rollback()
            raise

    def save_snapshots(self, snapshots: List[OrderBookSnapshot]):
        """
        批量保存盘口快照
        """
        if not snapshots: return
            
        try:
            # Snapshots 因为 ID 是 uuid，通常直接 insert 即可
            # 如果也想防重，可以把 snapshot_id 改成基于 revision 的 hash
            self.db.bulk_save_objects(snapshots)
            self.db.commit()
            logger.info(f"成功归档 {len(snapshots)} 个盘口快照")
        except Exception as e:
            logger.error(f"快照写入失败: {e}")
            self.db.rollback()
            raise