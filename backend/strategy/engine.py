# backend/strategy/engine.py
import pandas as pd
import numpy as np

class BacktestEngine:
    def __init__(self, data, contract_close_ts, force_close_minutes=10, enable_slippage=False, contract_type='PH'):
        """
        :param contract_type: 'PH' (1小时) 或 'QH' (15分钟)
        """
        self.data = data
        self.contract_close_ts = contract_close_ts
        self.force_close_minutes = force_close_minutes
        self.enable_slippage = enable_slippage
        
        # 【新增】费率参数 (EUR/MWh)
        self.fee_trading = 0.22
        self.fee_clearing = 0.01
        self.total_fee_per_mwh = self.fee_trading + self.fee_clearing
        
        # 【新增】根据合约类型确定时长系数 (小时)
        # PH: 1小时, QH: 0.25小时
        self.duration_hours = 0.25 if contract_type == 'QH' else 1.0

        # 状态变量
        self.cash = 0.0
        self.current_position = 0.0
        self.history = []
        
        # 统计变量
        self.total_slippage_cost = 0.0
        self.total_transaction_fees = 0.0 # 【新增】总手续费统计

        # 单步临时状态
        self.active_orders = [] 
        self.current_slippage_cost = 0.0
        self.current_fee_cost = 0.0      # 【新增】单步手续费
        self.current_action = "HOLD"
        self.current_signal = ""
        self.current_trade_vol = 0.0
        self.current_row = {}
        self.is_force_closing = False
        self.strategy = None

    def execute_order(self, target_vol, reason=""):
        """
        执行订单逻辑：计算滑点 + 计算手续费
        """
        trade_vol = target_vol - self.current_position
        if abs(trade_vol) < 1e-6:
            return

        current_price = float(self.current_row.get('close', 0))
        
        # 1. 计算滑点成本 (Slippage)
        slippage_cost = 0.0
        if self.enable_slippage:
            slippage_rate = 0.0002 
            # 冲击成本模型：量越大，滑点越大
            impact = (abs(trade_vol) / 10.0) * 0.05 
            slippage_per_unit = current_price * (slippage_rate + impact / 100.0)
            slippage_cost = abs(trade_vol) * slippage_per_unit
        
        # 2. 【新增】计算交易与清算费 (Transaction & Clearing Fees)
        # 费用 = 交易量(MW) * 时长(h) * 费率(EUR/MWh)
        # 例如 10MW 的 QH 合约 = 10 * 0.25 * 0.23 = 0.575 EUR
        fee_cost = abs(trade_vol) * self.duration_hours * self.total_fee_per_mwh

        # 3. 更新资金 (扣除成本)
        # 卖出得钱，买入花钱。成本永远是扣除。
        cost_change = -(trade_vol * current_price) - slippage_cost - fee_cost
        self.cash += cost_change
        
        # 4. 更新持仓
        self.current_position = target_vol
        
        # 5. 记录单步统计
        self.current_trade_vol = abs(trade_vol)
        self.current_action = "BUY" if trade_vol > 0 else "SELL"
        if reason == "FORCE_CLOSE": self.current_action = "FORCE_CLOSE"
        
        self.current_signal = reason
        self.current_slippage_cost += slippage_cost
        self.current_fee_cost += fee_cost
        
        # 6. 累加总统计
        self.total_slippage_cost += slippage_cost
        self.total_transaction_fees += fee_cost

    def place_order(self, target_vol, type='MARKET', reason="", ttl=1):
        self.execute_order(target_vol, reason)

    def _match_orders(self, candle, idx):
        # 简化版：暂不处理 Limit Order 挂单簿，直接市价成交
        pass

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
        deadline = self.contract_close_ts - pd.Timedelta(minutes=self.force_close_minutes)
        
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
                "fee_cost": self.current_fee_cost, # 记录这笔费
                "cash": self.cash,
                "equity": total_equity,
                "trade_vol": self.current_trade_vol 
            })

    def get_results(self):
        return {
            "history": self.history,
            "total_slippage": self.total_slippage_cost,
            "total_fees": self.total_transaction_fees
        }