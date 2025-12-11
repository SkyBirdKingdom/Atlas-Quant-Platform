# backend/services/live_runner.py
import logging
from sqlalchemy.orm import Session
from ..strategy.strategies import DynamicConfigStrategy
from .feature_engine import get_latest_features

logger = logging.getLogger("LiveRunner")

class LiveContext:
    """
    模拟实盘环境的上下文
    用于欺骗策略对象，让它以为自己在引擎里运行
    """
    def __init__(self):
        self.orders = []
    
    def execute_order(self, target_pos):
        # 在实盘阶段，这里应该对接下单接口
        # 目前我们只做信号监控，所以只记录日志
        self.orders.append(target_pos)
        logger.info(f"⚡ [实盘模拟下单] 目标仓位调整为: {target_pos} MW")

def run_live_analysis(db: Session, area: str):
    """
    对指定区域执行一次实盘策略检查
    """
    # 1. 获取最近的数据 (带指标)
    # 必须足够长以计算 MACD(26) 和 SMA(200)，建议 300+
    df = get_latest_features(db, area, lookback=300)
    
    if df.empty:
        logger.warning(f"[{area}] 数据不足，跳过分析")
        return None
    
    # 2. 初始化策略
    strategy = DynamicConfigStrategy()
    
    # 3. 注入实盘环境
    # TODO: 未来这里应该从数据库读取真实的当前持仓
    # 现在假设我们空仓 (0.0)，这样容易触发买入信号用于测试
    strategy.position = 0.0 
    strategy.set_engine(LiveContext())
    strategy.init()
    
    # 4. 提取最新的一根 K 线 (Last Row)
    # df 是按时间升序排列的 (旧->新)，所以取 iloc[-1]
    latest_bar = df.iloc[-1]
    latest_timestamp = df.index[-1]
    
    # 转换为 dict 传给策略 (兼容 backtest 格式)
    # pandas series 转 dict 包含所有指标列
    row_dict = latest_bar.to_dict()
    row_dict['timestamp'] = latest_timestamp
    
    logger.info(f"[{area}] 正在分析 {latest_timestamp} 的信号... (RSI: {row_dict.get('RSI_14', 0):.2f})")
    
    # 5. 执行策略逻辑
    # 这里的 next 只跑一次，针对当前最新时刻
    strategy.next(row_dict)
    
    # 6. 收集结果 (从 logs 或 engine 里的 orders)
    # 如果策略里触发了 log，我们在这里最好能拿到
    # 简单起见，我们看策略有没有产生 execute_order
    
    signal = "NEUTRAL"
    if strategy.engine.orders:
        target = strategy.engine.orders[-1]
        if target > 0: signal = "BUY"
        elif target == 0: signal = "SELL"
    
    return {
        "area": area,
        "time": latest_timestamp,
        "rsi": row_dict.get('RSI_14'),
        "signal": signal,
        "logs": strategy.logs
    }