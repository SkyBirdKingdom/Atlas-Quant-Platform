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

    def process_historical_revisions_response(self, data: Dict, meta: Dict = {}) -> Dict[str, List]:
        """
        【修复版】解析历史归档数据
        同时返回：
        1. snapshots: 用于快速恢复
        2. ticks: 用于完整回放 (补全 isSnapshot=False 的丢失数据)
        """
        snapshots = []
        ticks = []
        
        revisions = data.get("revisions", [])
        contract_id = data.get("contractId")
        contract_name = meta.get("contract_name")
        delivery_area = data.get("deliveryArea") or meta.get("delivery_area", "UNKNOWN")
        delivery_start = meta.get("delivery_start")
        delivery_end = meta.get("delivery_end")

        for rev in revisions:
            rev_num = rev.get("revision")
            is_snapshot = rev.get("isSnapshot", False)
            
            # --- 1. 处理 Snapshot (保持原有逻辑) ---
            if is_snapshot:
                # 提取买卖单
                buy_orders = rev.get("buyOrders", [])
                sell_orders = rev.get("sellOrders", [])
                
                # 排序
                bids = sorted(
                    [[float(o.get("price", 0)), float(o.get("volume", 0))] for o in buy_orders if not o.get("deleted")],
                    key=lambda x: x[0], reverse=True
                )
                asks = sorted(
                    [[float(o.get("price", 0)), float(o.get("volume", 0))] for o in sell_orders if not o.get("deleted")],
                    key=lambda x: x[0]
                )
                
                # 尝试获取时间戳
                ts_str = rev.get("updatedAt")
                if not ts_str and buy_orders: ts_str = buy_orders[0].get("updatedTime")
                timestamp = self.parse_iso_time(ts_str) or datetime.utcnow()

                snapshot = OrderBookSnapshot(
                    snapshot_id=str(uuid.uuid4()), 
                    contract_id=contract_id,
                    contract_name=contract_name,
                    delivery_area=delivery_area,
                    delivery_start=delivery_start,
                    delivery_end=delivery_end,
                    timestamp=timestamp,
                    revision_number=rev_num,
                    bids=bids,
                    asks=asks,
                    is_native=True
                )
                snapshots.append(snapshot)

            # --- 2. 处理 Ticks (新增逻辑：解析 Delta) ---
            # 只有 isSnapshot=False 的 revision 才是真正的“流水”
            # Snapshot 虽然包含订单，但通常作为重置点，不直接转为 Tick 流以避免数据冗余
            if not is_snapshot:
                # 遍历买单和卖单
                for side, order_list in [('BUY', rev.get('buyOrders', [])), ('SELL', rev.get('sellOrders', []))]:
                    for order in order_list:
                        # 历史接口没有详细 Action，只能根据 deleted 判断
                        is_deleted = order.get("deleted", False)
                        tick_type = "CANCEL" if is_deleted else "UPDATE" 
                        # 注：API A 无法区分 NEW 和 MODIFY，统一视为 UPDATE (Upsert) 即可
                        
                        price = float(order.get("price", 0))
                        volume = float(order.get("volume", 0))
                        order_id = order.get("orderId")
                        updated_time = self.parse_iso_time(order.get("updatedTime"))
                        priority_time = self.parse_iso_time(order.get("priorityTime"))

                        # 生成确定性 ID
                        rev_key = str(rev_num) if rev_num is not None else str(updated_time.timestamp())
                        tick_id = self._generate_tick_id(contract_id, delivery_area, rev_key, order_id, tick_type)

                        tick = OrderFlowTick(
                            tick_id=tick_id,
                            contract_id=contract_id,
                            contract_name=contract_name,
                            delivery_area=delivery_area,
                            delivery_start=delivery_start,
                            delivery_end=delivery_end,
                            timestamp=updated_time,
                            price=price,
                            volume=volume,
                            side=side,
                            type=tick_type,
                            raw_action="deleted" if is_deleted else "update",
                            order_id=order_id,
                            revision_number=rev_num,
                            # 历史数据无法推算 aggressor
                            aggressor_side="NONE"
                        )
                        ticks.append(tick)

        return {'ticks': ticks, 'snapshots': snapshots}

    def _map_action_to_type(self, action: str) -> Optional[str]:
        if action in ["PartialExecution", "FullExecution"]: return "TRADE"
        if action in ["UserAdded"]: return "NEW"
        if action in ["UserDeleted", "SystemDeleted", "UserHibernated", "SystemHibernated"]: return "CANCEL"
        if action in ["UserModified", "SystemModified"]: return "UPDATE"
        return None
    
    def process_api_response(self, data: Dict, source_type: str = "Stream") -> List[OrderFlowTick]:
        """
        通用解析器，适用于 'ByUpdatedTime' (实时) 和 'ByContractId' (历史) 接口
        """
        ticks = []
        
        # 兼容两种外层结构：
        # 1. ByUpdatedTime: { contracts: [ { orders: [...] } ] }
        # 2. ByContractId: { revisions: [...] } -> 需要外层传入 contract_id 等元数据，这里假设已规范化
        
        contracts = data.get("contracts", [])
        
        # 如果是 ByContractId 接口返回的直接是顶层结构，可能需要不同的入口逻辑
        # 这里演示处理标准的 contracts 列表结构
        for contract in contracts:
            contract_id = contract.get("contractId")
            area = data.get("deliveryArea") or contract.get("deliveryArea") # Area 可能在顶层或合约层
            
            orders = contract.get("orders", [])
            for order in orders:
                ord_id = order.get("orderId")
                side = order.get("side") # Buy/Sell
                
                revisions = order.get("revisions", [])
                for rev in revisions:
                    # 提取核心字段
                    updated_time_str = rev.get("updatedTime")
                    if not updated_time_str: continue
                    
                    updated_time = datetime.fromisoformat(updated_time_str.replace('Z', '+00:00'))
                    
                    is_snapshot = rev.get("isSnapshot", False)
                    is_deleted = rev.get("deleted", False)
                    
                    # 构建 Tick
                    tick = OrderFlowTick(
                        contract_id=contract_id,
                        delivery_area=area,
                        timestamp=updated_time,
                        priority_time=self._parse_time(rev.get("priorityTime")),
                        order_id=ord_id,
                        revision_number=rev.get("revisionNumber", 0),
                        price=rev.get("price"),
                        volume=rev.get("volume"),
                        side=side,
                        state=rev.get("state"),
                        action=rev.get("action"),
                        is_snapshot=is_snapshot,
                        is_deleted=is_deleted,
                        source_type=source_type
                    )
                    ticks.append(tick)
                    
        return ticks

    def _parse_time(self, time_str):
        if not time_str: return None
        return datetime.fromisoformat(time_str.replace('Z', '+00:00'))