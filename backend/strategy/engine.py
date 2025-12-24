# backend/strategy/engine.py
import pandas as pd
import numpy as np
from datetime import timedelta
import uuid

class Order:
    def __init__(self, target_pos, type='MARKET', limit_price=None, ttl=60, reason=""):
        self.id = str(uuid.uuid4())
        self.target_pos = target_pos
        self.type = type # 'MARKET' or 'LIMIT'
        self.limit_price = limit_price
        self.ttl = ttl 
        self.created_at_idx = None 
        self.reason = reason
        self.status = 'PENDING'

class BacktestEngine:
    # 【修正】构造函数增加 contract_type，默认为 PH
    def __init__(self, data_df, close_ts, force_close_minutes=0, enable_slippage=True, contract_type='PH'):
        self.data = data_df
        self.contract_close_ts = close_ts 
        self.force_close_minutes = force_close_minutes
        self.enable_slippage = enable_slippage 
        
        # === 费率模型 ===
        # 交易费 0.22 + 清算费 0.01 = 0.23 EUR/MWh
        self.fee_rate_per_mwh = 0.23
        
        # PH = 1小时, QH = 0.25小时
        self.duration_hours = 0.25 if contract_type == 'QH' else 1.0

        self.history = [] 
        self.strategy = None
        
        self.cash = 0.0 
        self.current_position = 0.0
        
        # 统计
        self.total_slippage_cost = 0.0
        self.total_fee_cost = 0.0  # 【新增】
        
        # 单步状态
        self.current_slippage_cost = 0.0
        self.current_fee_cost = 0.0
        self.current_action = "" 
        self.current_signal = ""
        self.current_trade_vol = 0.0
        
        self.active_orders = [] 
        self.is_force_closing = False

    def run(self, strategy_class, **params):
        self.strategy = strategy_class()
        self.strategy.set_engine(self)
        for k, v in params.items():
            setattr(self.strategy, k, v)
        self.strategy.init()
        self._run_loop()
        return self.get_results()

    def run_custom_strategy(self, strategy_instance):
        self.strategy = strategy_instance
        self.strategy.set_engine(self)
        self.strategy.init()
        self._run_loop()
        return self.get_results()

    def _run_loop(self):
        deadline = self.contract_close_ts - timedelta(minutes=self.force_close_minutes)
        
        for idx, (timestamp, row) in enumerate(self.data.iterrows()):
            candle = row.to_dict()
            candle['timestamp'] = timestamp
            
            # 重置单步状态
            self.current_slippage_cost = 0.0
            self.current_fee_cost = 0.0
            self.current_action = "HOLD"
            self.current_signal = ""
            self.current_trade_vol = 0.0
            self.current_row = candle 
            
            self._match_orders(candle, idx)
            
            if timestamp >= deadline:
                self.is_force_closing = True
            
            if self.is_force_closing:
                self.active_orders = [] 
                if abs(self.current_position) > 1e-6:
                    self.place_order(0, type='MARKET', reason="FORCE_CLOSE", ttl=1)
            else:
                self.strategy.next(candle)
            
            current_price = float(candle.get('close', 0))
            market_value = self.current_position * current_price
            total_equity = self.cash + market_value
            
            self.history.append({
                "time": timestamp,
                "type": candle.get('type', 'Unknown'),
                "open": float(candle.get('open', 0)),
                "high": float(candle.get('high', 0)),
                "low": float(candle.get('low', 0)),
                "close": float(candle.get('close', 0)),
                "price": current_price,
                "volume": float(candle.get('volume', 0)),
                "position": round(self.current_position, 1),
                "action": self.current_action,
                "signal": self.current_signal,
                "slippage_cost": self.current_slippage_cost,
                "fee_cost": self.current_fee_cost, # 记录费
                "cash": self.cash,
                "equity": total_equity,
                "trade_vol": self.current_trade_vol 
            })

    def place_order(self, target_pos, type='MARKET', limit_price=None, reason="", ttl=60):
        target_pos = round(target_pos, 1)
        for o in self.active_orders:
            if o.target_pos == target_pos and o.type == type:
                return
        order = Order(target_pos, type, limit_price, ttl, reason)
        self.active_orders.append(order)

    def execute_order(self, target_position, reason=""):
        self.place_order(target_position, type='MARKET', reason=reason)

    def _match_orders(self, candle, current_idx):
        if not self.active_orders: return
        market_vol = float(candle.get('volume', 0))
        
        # 【防御机制】如果市场无量，坚决不成交
        if market_vol <= 0: return

        open_p = float(candle.get('open', 0))
        high_p = float(candle.get('high', 0))
        low_p = float(candle.get('low', 0))
        
        remaining_orders = []
        available_liquidity = market_vol 
        
        for order in self.active_orders:
            if order.created_at_idx is None:
                order.created_at_idx = current_idx
                remaining_orders.append(order)
                continue
            
            if current_idx - order.created_at_idx > order.ttl: continue 
            if available_liquidity <= 0:
                remaining_orders.append(order)
                continue

            exec_price = None
            is_buy = order.target_pos > self.current_position
            
            if order.type == 'MARKET':
                exec_price = open_p
            elif order.type == 'LIMIT':
                if is_buy:
                    if low_p <= order.limit_price:
                        exec_price = min(open_p, order.limit_price) if open_p < order.limit_price else order.limit_price
                else:
                    if high_p >= order.limit_price:
                        exec_price = max(open_p, order.limit_price) if open_p > order.limit_price else order.limit_price

            if exec_price is not None:
                desired_vol = round(abs(order.target_pos - self.current_position), 1)
                trade_vol = min(desired_vol, available_liquidity)
                trade_vol = round(trade_vol, 1)
                
                if trade_vol > 1e-6:
                    self._execute_trade(trade_vol, exec_price, is_buy, order.reason)
                    available_liquidity -= trade_vol
                    if abs(order.target_pos - self.current_position) > 1e-6:
                        remaining_orders.append(order)
            else:
                remaining_orders.append(order)
        self.active_orders = remaining_orders

    def _execute_trade(self, trade_vol, price, is_buy, reason):
        self.current_trade_vol += trade_vol 
        self.current_action = "BUY" if is_buy else "SELL"
        self.current_signal = reason
        
        trade_value = trade_vol * price
        
        if is_buy:
            self.cash -= trade_value
            self.current_position = round(self.current_position + trade_vol, 1)
        else:
            self.cash += trade_value
            self.current_position = round(self.current_position - trade_vol, 1)

        # 1. 修复后的滑点模型 (Slippage)
        slippage = 0.0
        if self.enable_slippage:
            # 基础滑点率 0.02% (万分之二)，每增加 10MW 冲击增加 50%
            base_slippage_rate = 0.0002 
            impact_factor = 1.0 + (trade_vol / 10.0) * 0.5
            
            # 单价滑点 = 价格 * (基础率 * 冲击系数)
            price_slippage = price * base_slippage_rate * impact_factor
            slippage = price_slippage * trade_vol
            
        self.cash -= slippage
        self.current_slippage_cost += slippage
        self.total_slippage_cost += slippage

        # 2. 费用模型 (Fees)
        # Fee = Volume * Duration * Rate
        fee = trade_vol * self.duration_hours * self.fee_rate_per_mwh
        self.cash -= fee
        self.current_fee_cost += fee
        self.total_fee_cost += fee

    def get_results(self):
        return {
            "history": self.history,
            "total_slippage": self.total_slippage_cost,
            "total_fees": self.total_fee_cost
        }