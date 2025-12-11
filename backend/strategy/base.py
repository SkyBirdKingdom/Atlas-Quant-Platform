# backend/strategy/base.py
from abc import ABC, abstractmethod

class Strategy(ABC):
    def __init__(self):
        self.position = 0.0 
        self.engine = None
        # 新增：最大持仓限制（由外部注入）
        self.max_pos = 0.0 

    def set_engine(self, engine):
        self.engine = engine

    @abstractmethod
    def init(self):
        pass

    @abstractmethod
    def next(self, row):
        pass

    # 修改：增加 reason 参数
    def set_target_position(self, target_vol, reason=""):
        """
        设置目标持仓
        reason: 交易原因/信号 (例如 "RSI_Oversold")
        """
        if self.engine:
            self.engine.execute_order(target_vol, reason)