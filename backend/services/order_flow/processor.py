# backend/services/order_flow/processor.py
import logging
from datetime import datetime
from typing import List, Dict, Optional
from ...models import OrderFlowTick, OrderBookSnapshot
import uuid
from decimal import Decimal, getcontext

logger = logging.getLogger("OrderFlowProcessor")
getcontext().prec = 40

class OrderFlowProcessor:
    """
    订单流数据清洗与加工核心
    修正了 Volume 计算逻辑：Remaining -> Trade Delta
    """

    def parse_iso_time(self, time_str: str) -> Optional[datetime]:
        if not time_str: return None
        if time_str.endswith('Z'):
            time_str = time_str[:-1]
        try:
            return datetime.fromisoformat(time_str)
        except ValueError:
            return None

    def process_recent_orders_response(self, area: str, data: Dict) -> List[OrderFlowTick]:
        """
        处理【近实盘】API 响应
        """
        ticks = []
        contracts = data.get("contracts", [])
        
        for contract in contracts:
            contract_id = contract.get("contractId")
            orders = contract.get("orders", [])
            
            for order in orders:
                order_id = order.get("orderId")
                side = order.get("side", "Unknown").upper()
                created_time_str = order.get("createdTime")
                created_dt = self.parse_iso_time(created_time_str)
                
                # 获取该订单的所有版本
                revisions = order.get("revisions", [])
                
                # 必须按 revisionNumber 排序，确保计算 Delta 的顺序正确
                revisions.sort(key=lambda x: x.get("revisionNumber", 0))
                
                # 【核心修正】维护本地状态以计算 Delta
                # 如果我们是从 UserAdded 开始的，last_remaining_vol 应该是 0 (因为添加前是0)
                # 但如果是从中间截断的，我们只能尽力而为
                last_remaining_vol = None 
                
                for i, rev in enumerate(revisions):
                    action = rev.get("action", "None")
                    if action in ["None", "Unknown"]: continue
                        
                    tick_type = self._map_action_to_type(action)
                    if not tick_type: continue
                    
                    # 1. 提取基础数据
                    current_remaining_vol = float(rev.get("volume", 0))
                    price = float(rev.get("price", 0))
                    updated_dt = self.parse_iso_time(rev.get("updatedTime"))
                    
                    # 2. 计算成交量/变动量 (Delta Volume)
                    calculated_volume = 0.0
                    
                    if tick_type == "NEW":
                        # 新单: Volume 就是当前的剩余量
                        calculated_volume = current_remaining_vol
                        # 更新状态
                        last_remaining_vol = current_remaining_vol
                        
                    elif tick_type in ["TRADE", "CANCEL", "UPDATE"]:
                        # 只有当我们知道上一个状态时，才能计算成交量/撤单量
                        if last_remaining_vol is not None:
                            # 变动量 = 上次剩余 - 本次剩余
                            # 例如: 上次10, 本次FullExecution(0) -> 成交10
                            # 例如: 上次10, 本次Partial(6) -> 成交4
                            

                            last_dec = Decimal(str(last_remaining_vol))
                            curr_dec = Decimal(str(current_remaining_vol))
                            delta = float(last_dec - curr_dec)
                            calculated_volume = max(0.0, delta) # 理论上应该是正数
                        else:
                            # 边界情况：API 返回的列表里第一条就是成交/撤单，且没给之前的状态
                            # 这时候我们不知道成交了多少。
                            # 策略：Phase 1 先标记为 0 (或者存 None)，依赖 remaining_volume 做后续分析
                            calculated_volume = 0.0
                            # logger.warning(f"无法计算成交量: {order_id} Rev {rev.get('revisionNumber')}")

                        # 更新状态
                        last_remaining_vol = current_remaining_vol

                    # 3. 推算主动方
                    aggressor_side = "NONE"
                    if tick_type == "TRADE" and created_dt and updated_dt:
                        delta_sec = (updated_dt - created_dt).total_seconds()
                        if delta_sec < 0.2:
                            aggressor_side = side 
                        else:
                            aggressor_side = "SELL" if side == "BUY" else "BUY"

                    # 4. 构建 Tick
                    tick = OrderFlowTick(
                        tick_id=str(uuid.uuid4()),
                        contract_id=contract_id,
                        contract_name=contract.get("contractName"),
                        delivery_start=self.parse_iso_time(contract.get("deliveryStart")),
                        delivery_end=self.parse_iso_time(contract.get("deliveryEnd")),
                        delivery_area=area,
                        timestamp=updated_dt,
                        price=price,
                        
                        # 【核心修正】
                        volume=calculated_volume,       # 这是我们要的“成交量”
                        remaining_volume=current_remaining_vol, # 这是API给的“剩余量”
                        
                        side=side,
                        type=tick_type,
                        raw_action=action,
                        aggressor_side=aggressor_side,
                        order_id=order_id,
                        revision_number=rev.get("revisionNumber")
                    )
                    ticks.append(tick)
                    
        return ticks

    def process_historical_revisions_response(self, data: Dict, meta: Dict = {}) -> List[OrderBookSnapshot]:
        """
        处理【历史】API 响应
        :param meta: 外部传入的合约元数据 {'contract_name', 'delivery_start', ...} 
                     因为 Interface A 的响应可能不包含这些详情
        """
        snapshots = []
        revisions = data.get("revisions", [])
        contract_id = data.get("contractId", "UNKNOWN")
        delivery_area = data.get("deliveryArea") or meta.get("delivery_area", "UNKNOWN")
        
        for rev in revisions:
            if rev.get("isSnapshot") is True:
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
                if not ts_str and buy_orders:
                    ts_str = buy_orders[0].get("updatedTime")
                
                snapshot = OrderBookSnapshot(
                    snapshot_id=str(uuid.uuid4()),
                    contract_id=contract_id,
                    
                    # 【新增】填充元数据 (优先用 meta 传入的)
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
        if action in ["PartialExecution", "FullExecution"]:
            return "TRADE"
        if action in ["UserAdded"]:
            return "NEW"
        if action in ["UserDeleted", "SystemDeleted", "UserHibernated", "SystemHibernated"]:
            return "CANCEL"
        if action in ["UserModified", "SystemModified"]:
            return "UPDATE"
        return None