# backend/services/order_flow/processor.py
import logging
import hashlib
from datetime import datetime
from typing import List, Dict, Optional
from decimal import Decimal, getcontext
import uuid
from ...models import OrderFlowTick, OrderBookSnapshot

getcontext().prec = 28
logger = logging.getLogger("OrderFlowProcessor")

class OrderFlowProcessor:
    
    def parse_iso_time(self, time_str: str) -> Optional[datetime]:
        if not time_str: return None
        if time_str.endswith('Z'): time_str = time_str[:-1]
        try:
            return datetime.fromisoformat(time_str)
        except ValueError:
            return None

    def _to_decimal(self, val):
        if val is None: return Decimal("0")
        return Decimal(str(val))

    def _generate_tick_id(self, contract_id, delivery_area, revision, order_id, action):
        """
        【关键】生成确定性 ID (Deterministic ID)
        只要业务要素一致，生成的 ID 永远一致。
        防止历史和实时数据打架。
        """
        raw_str = f"{contract_id}_{delivery_area}_{revision}_{order_id}_{action}"
        # 使用 MD5 生成定长 ID (或者直接用 raw_str 也行，如果不太长)
        return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

    def process_recent_orders_response(self, data: Dict) -> List[OrderFlowTick]:
        ticks = []
        contracts = data.get("contracts", [])
        delivery_area = data.get("deliveryArea", "UNKNOWN")
        
        for contract in contracts:
            contract_id = contract.get("contractId")
            contract_name = contract.get("contractName")
            delivery_start = self.parse_iso_time(contract.get("deliveryStart"))
            delivery_end = self.parse_iso_time(contract.get("deliveryEnd"))
            
            orders = contract.get("orders", [])
            for order in orders:
                order_id = order.get("orderId")
                side = order.get("side", "Unknown").upper()
                created_time_str = order.get("createdTime")
                created_dt = self.parse_iso_time(created_time_str)
                
                revisions = order.get("revisions", [])
                # 按 revision 排序很重要，确保状态计算正确
                revisions.sort(key=lambda x: x.get("revisionNumber", 0))
                
                last_remaining_vol = None 
                
                for rev in revisions:
                    action = rev.get("action", "None")
                    if action in ["None", "Unknown"]: continue
                        
                    tick_type = self._map_action_to_type(action)
                    if not tick_type: continue
                    
                    rev_num = rev.get("revisionNumber")
                    current_remaining_vol = self._to_decimal(rev.get("volume", 0))
                    price = float(rev.get("price", 0))
                    updated_dt = self.parse_iso_time(rev.get("updatedTime"))
                    
                    # 1. 计算 Delta
                    calculated_volume = Decimal("0")
                    if tick_type == "NEW":
                        calculated_volume = current_remaining_vol
                        last_remaining_vol = current_remaining_vol
                    elif tick_type in ["TRADE", "CANCEL", "UPDATE"]:
                        if last_remaining_vol is not None:
                            delta = last_remaining_vol - current_remaining_vol
                            calculated_volume = max(Decimal("0"), delta)
                        else:
                            calculated_volume = Decimal("0")
                        last_remaining_vol = current_remaining_vol

                    # 2. 推算主动方
                    aggressor_side = "NONE"
                    if tick_type == "TRADE" and created_dt and updated_dt:
                        delta_sec = (updated_dt - created_dt).total_seconds()
                        if delta_sec < 0.2:
                            aggressor_side = side 
                        else:
                            aggressor_side = "SELL" if side == "BUY" else "BUY"

                    # 3. 生成 ID
                    # 注意：如果 API 没给 revisionNumber (极端情况)，用 timestamp 代替
                    rev_key = str(rev_num) if rev_num is not None else str(updated_dt.timestamp())
                    tick_id = self._generate_tick_id(contract_id, delivery_area, rev_key, order_id, action)

                    tick = OrderFlowTick(
                        tick_id=tick_id,  # <--- 使用确定性 ID
                        contract_id=contract_id,
                        contract_name=contract_name,
                        delivery_area=delivery_area,
                        delivery_start=delivery_start,
                        delivery_end=delivery_end,
                        timestamp=updated_dt,
                        price=price,
                        volume=float(calculated_volume),
                        remaining_volume=float(current_remaining_vol),
                        side=side,
                        type=tick_type,
                        raw_action=action,
                        aggressor_side=aggressor_side,
                        order_id=order_id,
                        revision_number=rev_num
                    )
                    ticks.append(tick)
                    
        return ticks

    def process_historical_revisions_response(self, data: Dict, meta: Dict = {}) -> List[OrderBookSnapshot]:
        """
        处理历史快照 (保持不变，或同样应用确定性ID)
        """
        snapshots = []
        revisions = data.get("revisions", [])
        contract_id = data.get("contractId", "UNKNOWN")
        delivery_area = data.get("deliveryArea") or meta.get("delivery_area", "UNKNOWN")
        
        for rev in revisions:
            if rev.get("isSnapshot") is True:
                # 简化逻辑... (同之前)
                # Snapshot ID 也可以做成确定性的，例如 f"SNAP_{contract_id}_{revision}"
                # 这里暂且保留 uuid，因为 Snapshot 重复抓取的代价较小(通常一天只抓一次)
                
                buy_orders = rev.get("buyOrders", [])
                sell_orders = rev.get("sellOrders", [])
                
                bids = sorted(
                    [[float(o.get("price", 0)), float(o.get("volume", 0))] for o in buy_orders if not o.get("deleted")],
                    key=lambda x: x[0], reverse=True
                )
                asks = sorted(
                    [[float(o.get("price", 0)), float(o.get("volume", 0))] for o in sell_orders if not o.get("deleted")],
                    key=lambda x: x[0]
                )
                
                ts_str = rev.get("updatedAt")
                if not ts_str and buy_orders: ts_str = buy_orders[0].get("updatedTime")
                
                snapshot = OrderBookSnapshot(
                    snapshot_id=str(uuid.uuid4()), 
                    contract_id=contract_id,
                    contract_name=meta.get('contract_name'),
                    delivery_area=delivery_area,
                    delivery_start=meta.get('delivery_start'),
                    delivery_end=meta.get('delivery_end'),
                    timestamp=self.parse_iso_time(ts_str) or datetime.utcnow(),
                    revision_number=rev.get("revision"),
                    bids=bids,
                    asks=asks,
                    is_native=True
                )
                snapshots.append(snapshot)
                
        return snapshots

    def _map_action_to_type(self, action: str) -> Optional[str]:
        if action in ["PartialExecution", "FullExecution"]: return "TRADE"
        if action in ["UserAdded"]: return "NEW"
        if action in ["UserDeleted", "SystemDeleted", "UserHibernated", "SystemHibernated"]: return "CANCEL"
        if action in ["UserModified", "SystemModified"]: return "UPDATE"
        return None