# backend/strategy/strategies.py
from .base import Strategy

class DynamicConfigStrategy(Strategy):
    """
    【通用配置化策略 V2】
    支持 "指标 vs 数值" 以及 "指标 vs 指标"
    """
    max_pos = 5.0
    rules = {} 

    def init(self):
        if not isinstance(self.rules, dict):
            self.rules = {}

    def get_value(self, row, target):
        """
        辅助函数：尝试解析目标是 '数值' 还是 '指标名'
        """
        # 1. 尝试直接转数字
        try:
            return float(target)
        except (ValueError, TypeError):
            # 2. 转不了数字，说明是指标名 (例如 'SMA_50')
            # 从 row 中获取，如果 row 里没有，返回 None
            return row.get(target)

    def check_condition(self, row, conditions):
        if not conditions: return False
        
        for cond in conditions:
            # 左值 (LHS)
            lhs_name = cond.get("indicator")
            lhs_val = row.get(lhs_name)
            if lhs_val is None: return False
            
            # 右值 (RHS) - 可能是数字 30，也可能是字符串 "SMA_50"
            rhs_raw = cond.get("val")
            rhs_val = self.get_value(row, rhs_raw)
            if rhs_val is None: return False
            
            op = cond.get("op")
            
            # 执行比较
            if op == "<" and not (lhs_val < rhs_val): return False
            if op == ">" and not (lhs_val > rhs_val): return False
            if op == "=" and not (lhs_val == rhs_val): return False
            
        return True

    def next(self, row):
        buy_rules = self.rules.get("buy", [])
        sell_rules = self.rules.get("sell", [])
        
        is_buy_signal = self.check_condition(row, buy_rules)
        is_sell_signal = self.check_condition(row, sell_rules)
        
        current_pos = self.position
        
        # 优先处理买入
        if is_buy_signal:
            if current_pos < self.max_pos:
                reason = " & ".join([f"{c['indicator']}{c['op']}{c['val']}" for c in buy_rules])
                self.set_target_position(self.max_pos, reason=f"LONG: {reason}")

        # 处理卖出
        elif is_sell_signal:
            if current_pos > -self.max_pos:
                reason = " & ".join([f"{c['indicator']}{c['op']}{c['val']}" for c in sell_rules])
                self.set_target_position(-self.max_pos, reason=f"SHORT: {reason}")