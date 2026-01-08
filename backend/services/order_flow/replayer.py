# backend/services/order_flow/replayer.py
from datetime import datetime
from typing import Dict, List, Optional
import logging
from sqlalchemy.orm import Session
from ...models import OrderFlowTick

logger = logging.getLogger("OrderBookReplayer")

class OrderBookReplayer:
    """
    【高精度版】订单簿回放引擎
    策略：全量 Tick 回放 (Tick-Only Replay)
    放弃有损的 Snapshot，从合约历史的起点开始推演，确保 OrderID 和 PriorityTime 100% 准确。
    """
    def __init__(self, db: Session):
        self.db = db

    def get_order_book_at(self, contract_id: str, target_time: datetime) -> Dict:
        """
        构建指定时刻的完整订单簿
        """
        # 1. 直接拉取从“开天辟地”到 target_time 的所有 Ticks
        # Nord Pool 合约周期短，全量拉取通常只有几千/几万条，性能可控
        ticks = self.db.query(OrderFlowTick)\
            .filter(
                OrderFlowTick.contract_id == contract_id,
                OrderFlowTick.timestamp <= target_time
            )\
            .order_by(
                OrderFlowTick.timestamp.asc(), 
                OrderFlowTick.revision_number.asc()
            )\
            .all()

        if not ticks:
            logger.warning(f"合约 {contract_id} 在 {target_time} 前无任何数据")
            return {"timestamp": target_time, "bids": [], "asks": []}

        # 2. 内存构建订单簿 (Order Map)
        # Key: Order ID, Value: Order Detail
        active_orders: Dict[str, dict] = {}

        for tick in ticks:
            # 特殊情况：如果遇到 API 发送的 Full Snapshot (is_snapshot=True)
            # 这通常意味着系统重置，我们需要清空当前状态，以 Snapshot 为准
            # 注意：这需要 tick 对象里有 is_snapshot 字段 (我们在模型里加了)
            if getattr(tick, 'is_snapshot', False):
                # 只有当 Snapshot 包含完整订单列表时才清空重建
                # 由于我们在 Processor 里把 Snapshot 拆成了 ADD 类型的 Ticks，
                # 所以这里通常不需要特殊清空，除非是显式的 Reset 信号。
                # 简单起见，如果我们的 Processor 逻辑正确（将 snapshot 拆解为一个个 order），
                # 这里直接 apply 即可。
                pass

            self._apply_tick(active_orders, tick)

        # 3. 组装最终盘口
        return self._build_book(active_orders, target_time)

    def _apply_tick(self, book: Dict[str, dict], tick: OrderFlowTick):
        """
        核心状态机
        """
        # 判定删除逻辑
        # 1. 显式 Cancel / Delete
        # 2. 数量归零 (API: volume=0 implies removal)
        is_delete = False
        if tick.type == 'CANCEL' or 'Deleted' in (tick.raw_action or '') or tick.is_deleted:
            is_delete = True
        
        if is_delete or tick.volume <= 0:
            if tick.order_id in book:
                del book[tick.order_id]
        else:
            # 新增 或 修改 (Upsert)
            # 因为 tick 是按时间顺序来的，后面的 update 会直接覆盖前面的状态
            # 这就是 Event Sourcing 的魅力
            book[tick.order_id] = {
                "id": tick.order_id,
                "price": tick.price,
                "volume": tick.volume,
                "side": tick.side,
                # 优先使用 priority_time，如果没有则用更新时间
                # [cite_start]priority_time 是撮合排序的关键 [cite: 14, 18]
                "priority_time": tick.priority_time or tick.timestamp
            }

    def _build_book(self, order_map: Dict[str, dict], timestamp: datetime):
        bids = []
        asks = []

        for order in order_map.values():
            # 必须包含 order_id，这对策略非常重要
            entry = {
                "price": order["price"],
                "volume": order["volume"],
                "id": order["id"],
                "priority_time": order["priority_time"]
            }
            
            if order["side"] == 'Buy' or order["side"] == 'BUY':
                bids.append(entry)
            elif order["side"] == 'Sell' or order["side"] == 'SELL':
                asks.append(entry)

        # [cite_start]排序规则 (Nord Pool 标准) [cite: 15-18]
        # Bids: 价格从高到低 -> 时间从早到晚
        bids.sort(key=lambda x: (-x['price'], x['priority_time']))
        
        # Asks: 价格从低到高 -> 时间从早到晚
        asks.sort(key=lambda x: (x['price'], x['priority_time']))

        return {
            "timestamp": timestamp,
            "contract_id": list(order_map.values())[0]["id"].split('_')[0] if order_map else "",
            "bids": bids,
            "asks": asks
        }