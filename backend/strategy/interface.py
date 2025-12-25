# backend/strategy/interface.py
from abc import ABC, abstractmethod
from typing import Dict, Any, List

class IStrategy(ABC):
    """
    【Atlas V2 新一代策略接口】
    支持事件驱动：同时响应 K 线 (Candle) 和 逐笔成交 (Tick)。
    """

    def __init__(self):
        self.context = None # 执行上下文 (TradeEngine)

    def set_context(self, context):
        """注入执行环境 (TradeEngine)"""
        self.context = context

    @abstractmethod
    def init(self):
        """策略初始化 (加载参数、预计算等)"""
        pass

    @abstractmethod
    def on_candle(self, candle: Dict[str, Any]):
        """
        K线事件回调
        :param candle: 标准 K 线字典 {time, open, high, low, close, volume, ...}
        """
        pass

    @abstractmethod
    def on_tick(self, tick: Any):
        """
        Tick 事件回调 (高频数据)
        :param tick: OrderFlowTick 对象
        """
        pass

    @abstractmethod
    def on_order_status(self, order: Any):
        """订单状态变更回调"""
        pass