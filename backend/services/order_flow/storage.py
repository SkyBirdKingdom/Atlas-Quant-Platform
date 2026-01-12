# backend/services/order_flow/storage.py
import logging
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from typing import List
from ...models import OrderFlowTick, OrderBookSnapshot, OrderContract
from datetime import datetime, timezone
import os
import pandas as pd

logger = logging.getLogger("OrderFlowStorage")

class OrderFlowService:
    
    def __init__(self, db: Session):
        self.db = db
        self.base_data_dir = "data/order_flow"
    
    def save_ticks_to_parquet(self, ticks: List[OrderFlowTick], area: str, date_str: str, contract_id: str):
        """
        【新增】将 Ticks 存为本地 Parquet 文件 (冷数据)
        路径: ./data/order_flow/{area}/{date}/{contract_id}.parquet
        """
        if not ticks: return

        try:
            # 1. 构造目录
            # 最终路径如: data/order_flow/SE3/2025-01-01/
            dir_path = os.path.join(self.base_data_dir, area, date_str)
            os.makedirs(dir_path, exist_ok=True)

            file_path = os.path.join(dir_path, f"{contract_id}.parquet")

            # 2. 转换为 DataFrame
            # 直接提取对象属性，比 __dict__ 更干净
            data = []
            for t in ticks:
                data.append({
                    "tick_id": t.tick_id,
                    "revision_number": t.revision_number,
                    "is_snapshot": t.is_snapshot,
                    "order_id": t.order_id,
                    "side": t.side,
                    "price": t.price,
                    "volume": t.volume,
                    "updated_time": t.updated_time,
                    "priority_time": t.priority_time,
                    "is_deleted": t.is_deleted,
                    # 冗余字段可根据需要决定是否存储，Parquet 压缩率很高，建议保留
                    "contract_id": t.contract_id,
                    "delivery_area": t.delivery_area
                })
            
            df = pd.DataFrame(data)

            # 3. 写入 Parquet (使用 snappy 压缩，速度快且体积小)
            df.to_parquet(file_path, index=False, compression='snappy')
            logger.info(f"已归档文件: {file_path}")

        except Exception as e:
            logger.error(f"Parquet 文件写入失败 {contract_id}: {e}")
            raise

    def mark_contract_archived(self, contract_id: str):
        """
        【新增】将指定合约标记为已完成归档
        """
        try:
            self.db.query(OrderContract)\
                .filter(OrderContract.contract_id == contract_id)\
                .update({"is_archived": True, "updated_at": datetime.now(timezone.utc)})
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"更新合约 {contract_id} 归档状态失败: {e}")

    def save_contracts(self, contracts: List[OrderContract]):
        """
        【新增】批量保存合约元数据
        """
        if not contracts: return

        try:
            data_list = []
            for c in contracts:
                data_list.append({
                    "contract_id": c.contract_id,
                    "contract_name": c.contract_name,
                    "delivery_area": c.delivery_area,
                    "delivery_date_utc": c.delivery_date_utc,
                    "delivery_start": c.delivery_start,
                    "delivery_end": c.delivery_end,
                    "contract_open_time": c.contract_open_time,
                    "contract_close_time": c.contract_close_time,
                    "is_local_contract": c.is_local_contract,
                    "volume_unit": c.volume_unit,
                    "price_unit": c.price_unit,
                    # updated_at 由数据库自动处理或这里不传
                })

            # 使用 Upsert: 如果合约已存在则更新 (防止重复抓取时报错)
            stmt = insert(OrderContract).values(data_list)
            stmt = stmt.on_conflict_do_update(
                index_elements=['contract_id', 'delivery_area'],
                set_={
                    "contract_name": stmt.excluded.contract_name,
                    "contract_open_time": stmt.excluded.contract_open_time,
                    "contract_close_time": stmt.excluded.contract_close_time,
                    "updated_at": datetime.utcnow()
                }
            )
            
            self.db.execute(stmt)
            self.db.commit()
            # logger.info(f"已更新 {len(contracts)} 个合约信息")

        except Exception as e:
            logger.error(f"合约信息写入失败: {e}")
            self.db.rollback()
            raise

    def save_ticks(self, ticks: List[OrderFlowTick]):
        """
        【更新】批量保存 Tick 数据
        适配新的 String 主键 (tick_id) 和新增字段
        """
        if not ticks: return
        
        try:
            # 将对象转换为字典列表 (显式映射，确保安全)
            data_list = []
            for t in ticks:
                data_list.append({
                    "tick_id": t.tick_id,             # [关键] 对应新模型的主键
                    "contract_id": t.contract_id,
                    "delivery_area": t.delivery_area,
                    "revision_number": t.revision_number,
                    "is_snapshot": t.is_snapshot,
                    
                    "order_id": t.order_id,
                    "side": t.side,
                    "price": t.price,
                    "volume": t.volume,
                    
                    "updated_time": t.updated_time,   # [新增]
                    "priority_time": t.priority_time, # [新增]
                    
                    "is_deleted": t.is_deleted,
                    "root_updated_at": t.root_updated_at,
                    "created_at": t.created_at
                })
            
            # 幂等写入: 遇到 tick_id 冲突则跳过 (Do Nothing)
            # 因为 tick_id 是确定性哈希，重复数据生成的主键也一样
            stmt = insert(OrderFlowTick).values(data_list)
            stmt = stmt.on_conflict_do_nothing(index_elements=['tick_id'])
            
            self.db.execute(stmt)
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Tick 数据写入失败: {e}")
            self.db.rollback()
            raise

    def save_snapshots(self, snapshots: List[OrderBookSnapshot]):
        """
        批量保存盘口快照 (保持不变，或根据需要优化)
        """
        if not snapshots: return
            
        try:
            self.db.bulk_save_objects(snapshots)
            self.db.commit()
        except Exception as e:
            logger.error(f"快照写入失败: {e}")
            self.db.rollback()
            raise