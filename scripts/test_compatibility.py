# scripts/test_compatibility.py
import sys
import os
import logging
from decimal import Decimal
from datetime import datetime

# 路径设置
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.strategy.engine import TradeEngine
from backend.strategy.adapter import LegacyStrategyAdapter
from backend.strategy.strategies import DynamicConfigStrategy # 您的旧策略
from backend.core.logger import setup_logging

setup_logging()
logger = logging.getLogger("CompatTest")

def main():
    logger.info("🚀 开始旧策略兼容性测试...")

    # 1. 准备旧策略配置
    # 假设我们有一个简单的规则：RSI < 30 买入
    # 注意：这里直接使用您现有的策略类
    strategy_params = {
        "rules": {
            "buy": [{"indicator": "RSI_14", "op": "<", "val": 30}],
            "sell": [{"indicator": "RSI_14", "op": ">", "val": 70}]
        },
        "max_pos": 5.0
    }

    # 2. 实例化适配器 (Adapter)
    # 这相当于：adapter = NewStrategy(OldStrategy)
    adapter = LegacyStrategyAdapter(DynamicConfigStrategy, **strategy_params)
    
    # 3. 实例化新引擎 (TradeEngine)
    engine = TradeEngine(mode='PAPER')
    
    # 4. 绑定环境
    # 以前是 engine.run() 自动做，现在手动注入
    adapter.set_context(engine)
    adapter.init() # 触发旧策略的 init()

    logger.info("✅ 策略与引擎绑定成功")

    # 5. 构造模拟数据 (制造 RSI < 30 的场景)
    # 假设前几根 K 线都在跌
    prices = [100, 95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 40]
    
    logger.info("📉 开始推演 K 线流...")
    
    for i, p in enumerate(prices):
        # 构造 K 线字典
        candle = {
            "time": datetime.now(),
            "open": p + 1,
            "high": p + 2,
            "low": p - 1,
            "close": p,
            "volume": 1000,
            # 伪造一个 RSI 指标 (因为旧策略依赖 feature_engine 算的指标)
            # 实际运行中这些指标在 feed 进来前已经算好了
            "RSI_14": 80 - (i * 5) # RSI 逐渐下降: 80, 75, ... 20
        }
        
        # 【关键】调用新引擎的 update_candle
        # 引擎会调用 adapter.on_candle -> adapter 调用 legacy.next
        engine.update_candle(candle, adapter)
        
        # 打印状态
        current_rsi = candle["RSI_14"]
        logger.info(f"Step {i+1}: Price={p}, RSI={current_rsi}, Orders={len(engine.active_orders)}, Pos={engine.current_position}")

        # 检查是否触发了下单
        if len(engine.active_orders) > 0:
            logger.info("🎉 检测到订单生成！")
            order = engine.active_orders[0]
            logger.info(f"订单详情: {order.type} {order.target_pos} MW (Reason: {order.reason})")
            break

    # 6. 验证结果
    if engine.active_orders:
        logger.info("✅ 测试通过：旧策略成功在新引擎中触发下单。")
    else:
        logger.error("❌ 测试失败：RSI已极低但未触发下单，请检查适配器逻辑。")

if __name__ == "__main__":
    main()