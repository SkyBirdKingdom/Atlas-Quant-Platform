# backend/strategy/strategies.py
from .base import Strategy
import logging

class DynamicConfigStrategy(Strategy):
    """
    【通用配置化策略 V3 - 进阶版】
    支持：
    1. 指标 vs 数值 (RSI < 30)
    2. 指标 vs 指标 (Close > SMA_50)
    3. 止盈止损 (Take Profit / Stop Loss)
    """
    max_pos = 5.0
    # 新增风控参数
    take_profit_pct = 0.0 # 止盈百分比 (e.g. 0.05 = 5%)
    stop_loss_pct = 0.0   # 止损百分比 (e.g. 0.02 = 2%)
    
    rules = {} 

    def init(self):
        if not isinstance(self.rules, dict):
            self.rules = {}
        # 初始化入场价格，用于计算动态止盈止损
        self.entry_price = 0.0

    def get_indicator_value(self, row, target):
        """
        核心辅助函数：解析配置的值
        - 如果是数字 (30, 0.5)，直接返回
        - 如果是字符串 ("SMA_50", "close")，从 row 里取值
        """
        # 1. 尝试直接转数字
        try:
            return float(target)
        except (ValueError, TypeError):
            pass
            
        # 2. 如果是字符串，尝试从数据行中获取
        if isinstance(target, str):
            val = row.get(target)
            if val is not None:
                return float(val)
        
        return None

    def check_condition(self, row, conditions):
        if not conditions: return False
        
        for cond in conditions:
            # 左值 (LHS): 必须是指标名
            lhs_name = cond.get("indicator")
            lhs_val = row.get(lhs_name)
            
            # 右值 (RHS): 可能是数字，也可能是指标名
            rhs_raw = cond.get("val")
            rhs_val = self.get_indicator_value(row, rhs_raw)
            
            # 如果任何一个值取不到 (比如 SMA_50 在前50分钟是 NaN)，则条件不成立
            if lhs_val is None or rhs_val is None: 
                return False
            
            op = cond.get("op")
            
            # 执行比较
            if op == "<" and not (lhs_val < rhs_val): return False
            if op == ">" and not (lhs_val > rhs_val): return False
            if op == "=" and not (lhs_val == rhs_val): return False
            # 扩展：支持 >=, <=
            if op == ">=" and not (lhs_val >= rhs_val): return False
            if op == "<=" and not (lhs_val <= rhs_val): return False
            
        return True

    def next(self, row):
        # 1. 提取当前价格
        current_price = row.get('close')
        if current_price is None: return

        # 2. 风控检查：止盈止损 (仅当持仓不为0时)
        if self.position != 0 and self.entry_price > 0:
            pnl_pct = (current_price - self.entry_price) / self.entry_price
            
            # 如果做空，PnL 逻辑反过来
            if self.position < 0:
                pnl_pct = -pnl_pct
                
            # 止盈触发
            if self.take_profit_pct > 0 and pnl_pct >= self.take_profit_pct:
                self.log(f"💰 TAKE PROFIT: {pnl_pct:.2%}")
                self.set_target_position(0, reason="TP")
                self.entry_price = 0
                return # 本次循环结束，不再开新仓

            # 止损触发
            if self.stop_loss_pct > 0 and pnl_pct <= -self.stop_loss_pct:
                self.log(f"🛑 STOP LOSS: {pnl_pct:.2%}")
                self.set_target_position(0, reason="SL")
                self.entry_price = 0
                return

        # 3. 信号检查
        buy_rules = self.rules.get("buy", [])
        sell_rules = self.rules.get("sell", [])
        
        is_buy_signal = self.check_condition(row, buy_rules)
        is_sell_signal = self.check_condition(row, sell_rules)
        
        # 4. 执行逻辑
        # 只有当信号出现，且当前没有同向持仓时才执行
        if is_buy_signal:
            if self.position < self.max_pos:
                reason = " & ".join([f"{c['indicator']}{c['op']}{c['val']}" for c in buy_rules])
                self.log(f"LONG SIGNAL: {reason} | Price: {current_price}")
                self.set_target_position(self.max_pos, reason=f"LONG: {reason}")
                # 记录开仓均价 (简化处理，假设一次成交)
                self.entry_price = current_price

        elif is_sell_signal:
            if self.position > -self.max_pos:
                reason = " & ".join([f"{c['indicator']}{c['op']}{c['val']}" for c in sell_rules])
                self.log(f"SHORT SIGNAL: {reason} | Price: {current_price}")
                self.set_target_position(-self.max_pos, reason=f"SHORT: {reason}")
                self.entry_price = current_price