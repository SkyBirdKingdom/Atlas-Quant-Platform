# backend/strategy/engine.py
import pandas as pd
import numpy as np
from datetime import timedelta
import uuid

class Order:
    def __init__(self, target_pos, type='MARKET', limit_price=None, ttl=5, reason=""):
        self.id = str(uuid.uuid4())
        self.target_pos = target_pos
        self.type = type # 'MARKET' or 'LIMIT'
        self.limit_price = limit_price
        self.ttl = ttl 
        self.created_at_idx = None 
        self.reason = reason
        self.status = 'PENDING'

class BacktestEngine:
    def __init__(self, data_df, close_ts, force_close_minutes=0, enable_slippage=True):
        self.data = data_df
        self.contract_close_ts = close_ts 
        self.force_close_minutes = force_close_minutes
        self.enable_slippage = enable_slippage 
        
        self.history = [] 
        self.strategy = None
        
        self.cash = 0.0 
        self.current_position = 0.0
        
        self.total_slippage_cost = 0.0
        self.current_slippage_cost = 0.0
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

        deadline = self.contract_close_ts - timedelta(minutes=self.force_close_minutes)
        
        for idx, (timestamp, row) in enumerate(self.data.iterrows()):
            candle = row.to_dict()
            candle['timestamp'] = timestamp
            
            # 1. 重置单步状态
            self.current_slippage_cost = 0.0
            self.current_action = "HOLD"
            self.current_signal = ""
            self.current_trade_vol = 0.0
            self.current_row = candle 
            
            # 2. 撮合订单
            self._match_orders(candle, idx)
            
            # 3. 检查强平窗口
            if timestamp >= deadline:
                self.is_force_closing = True
            
            # 4. 策略/强平调度
            if self.is_force_closing:
                self.active_orders = [] 
                # 【精度修复】判断持仓是否为0时，允许极小误差
                if abs(self.current_position) > 1e-6:
                    self.place_order(0, type='MARKET', reason="FORCE_CLOSE", ttl=1)
            else:
                self.strategy.next(candle)
            
            # 5. 资产计算
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
                
                # 这里也 round 一下，确保历史记录好看
                "position": round(self.current_position, 1),
                
                "action": self.current_action,
                "signal": self.current_signal,
                "slippage_cost": self.current_slippage_cost,
                "cash": self.cash,
                "equity": total_equity,
                "trade_vol": self.current_trade_vol 
            })
            
        return self.get_results()

    def place_order(self, target_pos, type='MARKET', limit_price=None, reason="", ttl=60):
        # 【精度修复】下单时也对目标仓位做圆整，防止策略传进来奇怪的小数
        target_pos = round(target_pos, 1)
        
        for o in self.active_orders:
            if o.target_pos == target_pos and o.type == type:
                return
        
        order = Order(target_pos, type, limit_price, ttl, reason)
        self.active_orders.append(order)

    def execute_order(self, target_position, reason=""):
        self.place_order(target_position, type='MARKET', reason=reason)

    def _match_orders(self, candle, current_idx):
        if not self.active_orders:
            return

        market_vol = float(candle.get('volume', 0))
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
            
            if current_idx - order.created_at_idx > order.ttl:
                continue 

            if available_liquidity <= 0:
                remaining_orders.append(order)
                continue

            # 价格匹配逻辑
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
                # 【精度修复】计算需求量时保留6位小数，去掉尾数噪音
                desired_vol = round(abs(order.target_pos - self.current_position), 1)
                
                trade_vol = min(desired_vol, available_liquidity)
                
                # 【精度修复】再次确保成交量干净
                trade_vol = round(trade_vol, 1)
                
                if trade_vol > 1e-6:
                    self._execute_trade(trade_vol, exec_price, is_buy, order.reason)
                    available_liquidity -= trade_vol
                    
                    # 检查是否完成
                    # 如果剩余未成交量极小，认为订单完成
                    new_gap = abs(order.target_pos - self.current_position)
                    if new_gap > 1e-6:
                        # 还没完，保留订单继续挂（简化逻辑，不考虑部分成交后的 target 调整问题）
                        # 在真实撮合中，应该修改 order 的 remaining quantity
                        # 但这里我们简化为：只要没达到 target_pos，订单就留着
                        remaining_orders.append(order)
            else:
                remaining_orders.append(order)
        
        self.active_orders = remaining_orders

    def _execute_trade(self, trade_vol, price, is_buy, reason):
        self.current_trade_vol += trade_vol 
        self.current_action = "BUY" if is_buy else "SELL"
        self.current_signal = reason
        
        trade_value = trade_vol * price
        
        # 资金结算
        if is_buy:
            self.cash -= trade_value
            # 【精度修复】持仓更新后立即 Round
            self.current_position = round(self.current_position + trade_vol, 1)
        else:
            self.cash += trade_value
            # 【精度修复】持仓更新后立即 Round
            self.current_position = round(self.current_position - trade_vol, 1)

        # 滑点计算
        cost = 0.0
        if self.enable_slippage:
            base_vol = price * 0.01
            unit_slippage = base_vol * 1.0 
            cost = unit_slippage * trade_vol
            if np.isnan(cost): cost = 0.0
            
        self.cash -= cost
        self.current_slippage_cost += cost
        self.total_slippage_cost += cost

    def get_results(self):
        return pd.DataFrame(self.history)