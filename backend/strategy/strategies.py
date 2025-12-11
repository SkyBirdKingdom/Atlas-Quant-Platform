# backend/strategy/strategies.py
from .base import Strategy

class NaiveStrategy(Strategy):
    """
    笨策略：始终保持满仓 (Base Position)
    """
    base_pos = 5.0 # 默认参数

    def init(self):
        pass

    def next(self, row):
        # 不管三七二十一，始终持有 base_pos
        self.set_target_position(self.base_pos)


class LiquidityRiskStrategy(Strategy):
    """
    智能策略：根据 PH/QH 阈值动态调整仓位
    """
    base_pos = 5.0
    reduced_pos = 2.0
    ph_threshold = 40
    qh_threshold = 10

    def init(self):
        pass

    def next(self, row):
        # 1. 判断是 PH 还是 QH
        c_type = row.get('type')
        limit = self.ph_threshold if c_type == 'PH' else self.qh_threshold
        
        # 2. 检查流动性
        market_vol = row['volume']
        
        # 3. 决策
        if market_vol < limit:
            # 危险！降级
            self.set_target_position(self.reduced_pos)
        else:
            # 安全，满仓
            self.set_target_position(self.base_pos)