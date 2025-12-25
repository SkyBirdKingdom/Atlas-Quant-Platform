# backend/strategy/adapter.py
import logging
from .interface import IStrategy
from .base import Strategy as LegacyBaseStrategy

logger = logging.getLogger("StrategyAdapter")

class LegacyStrategyAdapter(IStrategy):
    """
    【遗产兼容适配器】
    用途：将旧版 Strategy (只响应 next/K线) 封装为新版 IStrategy。
    原理：
    1. 劫持旧策略的 self.engine。
    2. 当旧策略调用 self.engine.execute_order 时，转发给新版 self.context。
    3. 忽略 on_tick 事件 (旧策略不懂 Tick)。
    """

    def __init__(self, legacy_strategy_cls, **params):
        super().__init__()
        # 实例化旧策略
        self.legacy: LegacyBaseStrategy = legacy_strategy_cls()
        
        # 注入参数 (兼容旧版 setattr 逻辑)
        for k, v in params.items():
            if hasattr(self.legacy, k):
                setattr(self.legacy, k, v)
            else:
                # 如果是 dynamic rules，可能直接存在 self.rules 里
                if k == 'rules':
                    self.legacy.rules = v
        
        # 【核心魔法】劫持引擎
        # 旧策略调用 self.engine.execute_order(...)
        # 我们把 self (Adapter) 伪装成 engine 传进去
        self.legacy.set_engine(self)

    def init(self):
        """调用旧策略的 init"""
        self.legacy.init()
        logger.info(f"已加载旧版策略: {self.legacy.__class__.__name__}")

    # --- 伪装成旧版 Engine 的方法 ---
    def execute_order(self, target_vol, reason=""):
        """
        旧策略以为自己在调用 Engine，实际上调用的是 Adapter。
        Adapter 将其转发给新版 Context (TradeEngine)。
        """
        if self.context:
            # 转发给新引擎
            self.context.execute_order(target_vol, reason=reason)
        else:
            logger.warning("策略试图下单，但未绑定执行上下文 (Context is None)")

    # --- 实现新版接口 ---
    def on_candle(self, candle: dict):
        """
        当新引擎收到 K 线时，转换格式并喂给旧策略
        """
        # 旧策略的 next(row) 需要一个 dict
        # 确保 candle 包含 timestamp, close 等字段
        # 如果新引擎传来的 candle 字段名有变化，在这里做 Mapping
        row = candle.copy()
        
        # 同步持仓状态：旧策略可能读取 self.position
        if self.context:
            self.legacy.position = float(self.context.current_position)
        
        # 执行旧逻辑
        self.legacy.next(row)

    def on_tick(self, tick):
        # 旧策略不支持 Tick，直接忽略
        pass

    def on_order_status(self, order):
        # 旧策略通常不处理订单回调，忽略
        pass