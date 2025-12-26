# backend/services/live_trader.py
import json
import os
import logging
from datetime import datetime, timedelta, timezone

# 引入核心组件
from ..strategy.engine import TradeEngine
from ..strategy.strategies import DynamicConfigStrategy
from ..strategy.adapter import LegacyStrategyAdapter
from .feature_engine import get_latest_features_df # 假设您有这个函数获取 K 线特征
from ..database import SessionLocal
from .order_flow.manager import OrderFlowManager

logger = logging.getLogger("LiveTrader")

# 状态持久化文件 (Phase 1 简化版，Phase 3 可升级为 Redis/DB)
STATE_FILE = "live_trader_state.json"

class LiveTrader:
    """
    【实盘/模拟盘 主控程序】
    职责：
    1. 维护 TradeEngine 的持久化状态
    2. 协调 K 线数据 (驱动策略) 和 Tick 数据 (驱动撮合)
    3. 执行定时循环
    """
    
    def __init__(self, area: str = "SE3", mode: str = "PAPER"):
        self.area = area
        self.mode = mode.upper() # PAPER or LIVE
        
        # 2. 初始化引擎
        # 注意：这里我们不需要每次都 create，而是优先从本地恢复
        self.engine = TradeEngine(mode=self.mode, enable_slippage=True)
        self.engine.cash = 40000.0  # 初始资金 40,000 欧元
        self._load_state()
        
        # 3. 初始化策略 (通过适配器)
        # TODO: 后期应从数据库读取策略配置
        strategy_config = {
            "rules": {
                "buy": [{"indicator": "RSI_14", "op": "<", "val": 50}],
                "sell": [{"indicator": "RSI_14", "op": ">", "val": 70}]
            },
            "max_pos": 5.0
        }
        self.strategy = LegacyStrategyAdapter(DynamicConfigStrategy, **strategy_config)
        self.strategy.set_context(self.engine)
        self.strategy.init()

    def run_tick(self):
        """
        【心跳函数】
        建议调度频率：每 15 分钟或 1 小时 (取决于数据延迟和策略频率)
        """
        logger.info(f"💓 [{self.area}|{self.mode}] 开始执行心跳循环...")
        db = SessionLocal()
        try:
            # 初始化数据管家
            manager = OrderFlowManager(db)
            # 获取最新 K 线数据，计算指标 (RSI等)
            self._run_strategy_step(db)
            
            # Step 2: 驱动执行 (Micro - Order Flow)
            # 获取最近的逐笔成交，尝试撮合挂单
            self._run_execution_step(manager)
            
            # Step 3: 保存状态
            self._save_state()
            
            # Step 4: 汇报状态
            self._report_status()
            
        except Exception as e:
            logger.error(f"❌ 心跳循环异常: {e}", exc_info=True)
        finally:
            db.close()

    def _run_strategy_step(self, db):
        """获取 K 线 -> 投喂给策略 -> 产生 Active Orders"""
        # 获取带有指标的 DataFrame (假设复用 Feature Engine)
        # lookback=100 确保指标计算准确
        df = get_latest_features_df(db, self.area, lookback=100)
        
        if df.empty:
            logger.warning("⚠️ 未获取到 K 线数据，跳过策略步骤")
            return

        # 取最新一根已完成的 K 线
        latest_candle = df.iloc[-1].to_dict()
        # 补全 timestamp (index 转 column)
        latest_candle['time'] = df.index[-1].to_pydatetime()

        rsi_val = latest_candle.get('RSI_14', 0)
        
        logger.info(f"📊 策略输入: Time={latest_candle['time']}, Close={latest_candle['close']}, RSI={rsi_val:.2f}")
        
        # 【关键】推入引擎 -> 触发 Adapter -> 触发旧策略 -> 产生 active_orders
        self.engine.update_candle(latest_candle, self.strategy)

    def _run_execution_step(self, manager: OrderFlowManager):
        """获取 Order Flow Ticks -> 投喂给引擎 -> 撮合 Active Orders"""
        
        # 调用 Manager，要求返回 ticks
        new_ticks = manager.sync_realtime_stream(self.area, return_ticks=True)

        if not new_ticks:
            # logger.info("🌊 无新 Tick 数据")
            return
        
        # 按时间正序排列
        new_ticks.sort(key=lambda x: x.timestamp)
        
        logger.info(f"⚡ 获取到 {len(new_ticks)} 条增量 Ticks，开始撮合...")
        
        # 喂入引擎
        for tick in new_ticks:
            self.engine.update_tick(tick, self.strategy)

    def _save_state(self):
        """持久化引擎状态 (持仓、资金、挂单)"""
        state = self.engine.get_state()
        # 加上时间戳
        state["_updated_at"] = datetime.now().isoformat()
        
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        logger.debug("💾 状态已保存")

    def _load_state(self):
        """恢复状态"""
        if not os.path.exists(STATE_FILE):
            return
            
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                self.engine.restore_state(state)
            logger.info(f"🔄 状态已恢复: Pos={self.engine.current_position}, Cash={self.engine.cash}")
        except Exception as e:
            logger.error(f"⚠️ 状态恢复失败: {e}")

    def _report_status(self):
        """打印当前账户概览"""
        pos = self.engine.current_position
        cash = self.engine.cash
        orders = len(self.engine.active_orders)
        logger.info(f"📈 [账户概览] 持仓: {pos} MW | 资金: {cash:.2f} € | 挂单数: {orders}")
        if orders > 0:
            for o in self.engine.active_orders:
                logger.info(f"   -> 挂单: {o.type} {o.target_pos} @ {o.limit_price or 'MKT'}")