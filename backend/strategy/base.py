# backend/strategy/base.py
from abc import ABC, abstractmethod

class Strategy(ABC):
    def __init__(self):
        self.position = 0.0 # 当前持仓
        self.cash = 0.0     # 现金 (在本系统中主要用于计算累计滑点节省，逻辑略有不同)
        self.logs = []      # 交易日志
        self.engine = None  # 引用引擎实例

    def set_engine(self, engine):
        self.engine = engine

    def log(self, message, time=None):
        """记录日志"""
        if time:
            self.logs.append(f"{time}: {message}")
        else:
            self.logs.append(message)

    @abstractmethod
    def init(self):
        """初始化逻辑 (比如计算指标)"""
        pass

    @abstractmethod
    def next(self, row):
        """
        核心逻辑：每一行数据(candle)过来时调用一次
        row: 包含 time, price, volume, type(PH/QH) 等字段的字典
        """
        pass

    # --- 交易动作 ---
    def set_target_position(self, target_vol):
        """
        设置目标持仓。引擎会自动计算需要买/卖多少，并计算滑点。
        """
        if self.engine:
            self.engine.execute_order(target_vol)