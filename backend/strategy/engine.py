# backend/strategy/engine.py
import pandas as pd
import numpy as np
from datetime import timedelta
import uuid
import logging
from decimal import Decimal, getcontext, ROUND_HALF_UP

# 设置足够高的计算精度
getcontext().prec = 40

logger = logging.getLogger("BacktestEngine")

class Order:
    def __init__(self, target_pos, type='MARKET', limit_price=None, ttl=60, reason=""):
        self.id = str(uuid.uuid4())
        # 这里的 target_pos 也要清洗，防止策略传进来 1.00000000001
        self.target_pos = self._clean(target_pos) if target_pos is not None else Decimal("0")
        self.type = type 
        self.limit_price = self._clean(limit_price) if limit_price is not None else None
        self.ttl = ttl 
        self.created_at_idx = None 
        self.reason = reason
        self.status = 'PENDING'
    
    @staticmethod
    def _clean(val):
        if isinstance(val, (float, int)):
            # 使用字符串转换避免二进制引入误差
            return Decimal(str(val))
        if isinstance(val, Decimal):
            return val
        return Decimal(str(val))

class BacktestEngine:
    def __init__(self, data_df, close_ts, force_close_minutes=0, enable_slippage=True, contract_type='PH'):
        # 修复索引问题
        self.data = data_df.reset_index()
        self.data.rename(columns={'timestamp': 'time', 'index': 'time'}, inplace=True)
        
        self.contract_close_ts = close_ts 
        self.force_close_minutes = int(force_close_minutes)
        self.enable_slippage = enable_slippage 
        
        # 费率常量 (Decimal)
        self.fee_rate_per_mwh = Decimal("0.23")
        self.duration_hours = Decimal("0.25") if contract_type == 'QH' else Decimal("1.0")

        self.history = [] 
        self.strategy = None
        
        # 核心状态 (Decimal)
        self.cash = Decimal("0.0")
        self.current_position = Decimal("0.0")
        
        # 统计 (Decimal)
        self.total_slippage_cost = Decimal("0.0")
        self.total_fee_cost = Decimal("0.0")
        
        # 单步状态
        self.current_slippage_cost = Decimal("0.0")
        self.current_fee_cost = Decimal("0.0")
        self.current_action = "HOLD"
        self.current_signal = ""
        self.current_trade_vol = Decimal("0.0")
        
        self.active_orders = [] 
        self.is_force_closing = False

    def clean_decimal(self, val):
        """
        【核心修复】输入数据清洗
        将 float 转换为 Decimal，并截断微小的浮点噪声 (保留10位小数)
        这能解决 100.0 变成 100.0000000000001 的问题
        """
        if val is None:
            return Decimal("0")
        
        d_val = Decimal(str(val)) if isinstance(val, (float, int)) else Decimal(val)
        
        # 量化到 1e-10 (足够覆盖电力市场的精度，同时过滤浮点噪声)
        return d_val.quantize(Decimal("1.0000000000"), rounding=ROUND_HALF_UP)

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
        
        for idx, row in self.data.iterrows():
            timestamp = row['time']
            candle = row.to_dict()
            
            # Reset step state
            self.current_slippage_cost = Decimal("0.0")
            self.current_fee_cost = Decimal("0.0")
            self.current_action = "HOLD"
            self.current_signal = ""
            self.current_trade_vol = Decimal("0.0")
            self.current_row = candle 
            
            if timestamp >= deadline:
                self.is_force_closing = True
            
            if self.is_force_closing:
                if abs(self.current_position) > Decimal("1e-6"):
                    self._force_close_position(candle)
                self.active_orders = []
            else:
                self._match_orders(candle, idx)
                self.strategy.next(candle)
            
            # 资产计算
            current_price = self.clean_decimal(candle.get('close', 0))
            market_value = self.current_position * current_price
            total_equity = self.cash + market_value
            
            # 记录历史 (转为 float 仅用于序列化，但源头数据是干净的)
            self.history.append({
                "time": timestamp,
                "type": candle.get('type', 'Unknown'),
                "open": float(candle.get('open', 0)),
                "high": float(candle.get('high', 0)),
                "low": float(candle.get('low', 0)),
                "close": float(candle.get('close', 0)),
                "price": float(current_price),
                "volume": float(candle.get('volume', 0)),
                "position": float(self.current_position),
                "action": self.current_action,
                "signal": self.current_signal,
                "slippage_cost": float(self.current_slippage_cost),
                "fee_cost": float(self.current_fee_cost),
                "cash": float(self.cash),
                "equity": float(total_equity),
                "trade_vol": float(self.current_trade_vol)
            })

    def _force_close_position(self, candle):
        price = self.clean_decimal(candle.get('close', 0))
        vol_to_close = abs(self.current_position)
        is_buy = self.current_position < 0 
        
        self._execute_trade(vol_to_close, price, is_buy, "FORCE_CLOSE")
        self.current_position = Decimal("0.0")

    def place_order(self, target_pos, type='MARKET', limit_price=None, reason="", ttl=60):
        # 对目标仓位也做清洗，确保它是干净的 Decimal
        target_pos = self.clean_decimal(target_pos)
        
        for o in self.active_orders:
            if o.target_pos == target_pos and o.type == type:
                return
        
        order = Order(target_pos, type, limit_price, ttl, reason)
        self.active_orders.append(order)

    def execute_order(self, target_position, reason=""):
        self.place_order(target_position, type='MARKET', reason=reason)

    def _match_orders(self, candle, current_idx):
        if not self.active_orders: return

        # 使用 clean_decimal 确保 input volume 干净
        market_vol = self.clean_decimal(candle.get('volume', 0))
        if market_vol <= 0: return

        open_p = self.clean_decimal(candle.get('open', 0))
        high_p = self.clean_decimal(candle.get('high', 0))
        low_p = self.clean_decimal(candle.get('low', 0))
        
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

            exec_price = None
            is_buy = order.target_pos > self.current_position
            
            if order.type == 'MARKET':
                exec_price = open_p
            elif order.type == 'LIMIT':
                limit_p = order.limit_price
                if is_buy:
                    if low_p <= limit_p:
                        exec_price = min(open_p, limit_p) if open_p < limit_p else limit_p
                else:
                    if high_p >= limit_p:
                        exec_price = max(open_p, limit_p) if open_p > limit_p else limit_p

            if exec_price is not None:
                desired_vol = abs(order.target_pos - self.current_position)
                trade_vol = min(desired_vol, available_liquidity)
                
                if trade_vol > Decimal("1e-6"):
                    self._execute_trade(trade_vol, exec_price, is_buy, order.reason)
                    available_liquidity -= trade_vol
                    
                    if abs(order.target_pos - self.current_position) > Decimal("1e-6"):
                        remaining_orders.append(order)
            else:
                remaining_orders.append(order)
        
        self.active_orders = remaining_orders

    def _execute_trade(self, trade_vol, price, is_buy, reason):
        self.current_trade_vol += trade_vol 
        self.current_action = "BUY" if is_buy else "SELL"
        # Force Close is just a signal, action remains BUY/SELL
        self.current_signal = reason
        
        trade_value = trade_vol * price
        
        if is_buy:
            self.cash -= trade_value
            self.current_position += trade_vol
        else:
            self.cash += trade_value
            self.current_position -= trade_vol

        slippage = Decimal("0.0")
        if self.enable_slippage:
            base_slippage_rate = Decimal("0.0002")
            impact_factor = Decimal("1.0") + (trade_vol / Decimal("10.0")) * Decimal("0.5")
            price_slippage = price * base_slippage_rate * impact_factor
            slippage = price_slippage * trade_vol
            
        self.cash -= slippage
        self.current_slippage_cost += slippage
        self.total_slippage_cost += slippage

        # Fee Calculation
        fee = trade_vol * self.duration_hours * self.fee_rate_per_mwh
        self.cash -= fee
        self.current_fee_cost += fee
        self.total_fee_cost += fee

    def get_results(self):
        return {
            "history": self.history,
            "total_slippage": float(self.total_slippage_cost),
            "total_fees": float(self.total_fee_cost)
        }