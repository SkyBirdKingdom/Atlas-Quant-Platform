# backend/services/order_flow/processor.py
import logging
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Optional
import uuid
from ...models import OrderFlowTick, OrderContract

logger = logging.getLogger("OrderFlowProcessor")

class OrderFlowProcessor:
    
    def parse_iso_time(self, time_str: str) -> Optional[datetime]:
        """解析 ISO8601 时间字符串 (带Z或不带)"""
        if not time_str: return None
        # 简单处理 Z 结尾
        if time_str.endswith('Z'): 
            time_str = time_str[:-1]
        try:
            dt = datetime.fromisoformat(time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None

    def _generate_tick_id(self, contract_id, revision, order_id, updated_time_str):
        """
        生成确定性 ID (Deterministic Hash)
        业务主键 = 合约 + 版本 + 订单号 + 更新时间
        """
        raw_str = f"{contract_id}_{revision}_{order_id}_{updated_time_str}"
        return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

    def process_contracts_response(self, data: Dict) -> List[OrderContract]:
        """
        【新增】处理 /ContractsIds/ByArea 接口响应
        """
        contracts = []
        delivery_area = data.get("deliveryArea")
        date_str = data.get("deliveryDateUtc") # "2026-01-08"
        
        # 转换 date 对象
        delivery_date = None
        if date_str:
            try:
                delivery_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        for c in data.get("contracts", []):
            contract = OrderContract(
                contract_id=c.get("contractId"),
                contract_name=c.get("contractName"),
                delivery_area=delivery_area,
                delivery_date_utc=delivery_date,
                delivery_start=self.parse_iso_time(c.get("deliveryStart")),
                delivery_end=self.parse_iso_time(c.get("deliveryEnd")),
                contract_open_time=self.parse_iso_time(c.get("contractOpenTime")),
                contract_close_time=self.parse_iso_time(c.get("contractCloseTime")),
                is_local_contract=c.get("isLocalContract", False)
            )
            contracts.append(contract)
        
        return contracts

    def process_historical_revisions_response(self, data: Dict) -> List[OrderFlowTick]:
        """
        【核心修复】处理 /OrderBook/ByContractId 接口响应
        完全适配用户提供的 JSON 结构
        """
        ticks = []
        
        # Root Level Info
        contract_id = data.get("contractId")
        delivery_area = data.get("deliveryArea")
        root_updated_at_str = data.get("updatedAt")
        root_updated_at = self.parse_iso_time(root_updated_at_str)
        
        # 遍历 Revisions
        revisions = data.get("revisions", [])
        for rev in revisions:
            rev_num = rev.get("revision")
            is_snapshot = rev.get("isSnapshot", False)
            
            # 遍历 Buy 和 Sell 列表
            # 结构: "buyOrders": [{ "orderId": "...", ... }, ... ]
            order_groups = [
                ("BUY", rev.get("buyOrders", [])),
                ("SELL", rev.get("sellOrders", []))
            ]
            
            for side, orders in order_groups:
                for order in orders:
                    # 提取 Order 字段
                    order_id = order.get("orderId")
                    price = float(order.get("price", 0.0))
                    volume = float(order.get("volume", 0.0))
                    is_deleted = order.get("deleted", False)
                    
                    priority_time_str = order.get("priorityTime")
                    updated_time_str = order.get("updatedTime")
                    
                    priority_time = self.parse_iso_time(priority_time_str)
                    updated_time = self.parse_iso_time(updated_time_str)
                    
                    # 生成 ID
                    # 注意：使用 updatedTime 字符串参与哈希，保证唯一性
                    tick_id = self._generate_tick_id(contract_id, rev_num, order_id, updated_time_str)
                    
                    # 实例化 Model (字段名必须与 models.py 完全一致)
                    tick = OrderFlowTick(
                        tick_id=tick_id,              # 对应 Model 中的 String 主键
                        contract_id=contract_id,
                        delivery_area=delivery_area,
                        revision_number=rev_num,
                        is_snapshot=is_snapshot,
                        order_id=order_id,
                        side=side,
                        price=price,
                        volume=volume,
                        updated_time=updated_time,    # 对应 API updatedTime
                        priority_time=priority_time,  # 对应 API priorityTime
                        is_deleted=is_deleted,
                        root_updated_at=root_updated_at # 记录这批数据的版本时间
                    )
                    ticks.append(tick)
                    
        return ticks
    
    # --- 1. 处理实时流 (修复报错) ---
    def process_api_response(self, data: Dict, source_type: str = "Stream") -> List[OrderFlowTick]:
        """
        【修复】处理 /Intraday/OrderRevisions/ByUpdatedTime 接口响应
        """
        ticks = []
        contracts = data.get("contracts", [])
        
        for contract in contracts:
            contract_id = contract.get("contractId")
            delivery_area = data.get("deliveryArea") or contract.get("deliveryArea")
            
            orders = contract.get("orders", [])
            for order in orders:
                order_id = order.get("orderId")
                side = order.get("side", "").upper() # Buy/Sell
                
                revisions = order.get("revisions", [])
                for rev in revisions:
                    updated_time_str = rev.get("updatedTime")
                    if not updated_time_str: continue
                    
                    updated_time = self.parse_iso_time(updated_time_str)
                    priority_time = self.parse_iso_time(rev.get("priorityTime"))
                    
                    rev_num = rev.get("revisionNumber", 0)
                    is_snapshot = rev.get("isSnapshot", False)
                    is_deleted = rev.get("deleted", False)
                    price = float(rev.get("price", 0))
                    volume = float(rev.get("volume", 0))

                    # 生成 ID
                    tick_id = self._generate_tick_id(contract_id, rev_num, order_id, updated_time_str)

                    tick = OrderFlowTick(
                        tick_id=tick_id,
                        contract_id=contract_id,
                        delivery_area=delivery_area,
                        revision_number=rev_num,
                        is_snapshot=is_snapshot,
                        order_id=order_id,
                        side=side,
                        price=price,
                        volume=volume,
                        updated_time=updated_time,
                        priority_time=priority_time,
                        is_deleted=is_deleted,
                        # 实时流没有 root_updated_at，可留空
                        created_at=datetime.now(timezone.utc)
                    )
                    ticks.append(tick)
        return ticks