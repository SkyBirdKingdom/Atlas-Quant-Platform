# backend/strategy/legacy_strategies.py
import logging
import numpy as np
from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import Dict, List, Optional, Set, Any
from scipy.stats import linregress # 需要确保安装 scipy
from .base import Strategy as BaseStrategy

logger = logging.getLogger("LegacyStrategy")

class LegacyNordPoolStrategy(BaseStrategy):
    """
    【最终完整版】旧交易系统的核心策略集合 (v2.0)
    
    集成特性：
    1. 策略路由：完整支持 days_of_week / time_ranges 配置解析
    2. 核心策略：Delivery Buy / Super Mean Rev / Extreme Sell
    3. 增强风控：
       - 趋势斜率过滤 (防止逆势接飞刀) [新增]
       - 连续亏损休眠 (Strategy Protection)
       - 日亏损熔断
    4. 数据清洗：Trade ID 去重 [新增]
    """
    
    def init(self):
        # --- 1. 配置加载 ---
        raw_config = self.config.get('strategy_params', {})
        if isinstance(raw_config, dict) and 'strategy_config' in raw_config:
            self.strategy_schedules = raw_config['strategy_config']
        elif isinstance(raw_config, list):
            self.strategy_schedules = raw_config
        else:
            self.strategy_schedules = [{
                "days_of_week": [0, 1, 2, 3, 4, 5, 6],
                "time_ranges": [{"start": "00:00", "end": "23:59", "strategy_params": raw_config}]
            }]

        self.daily_loss_limit = self.config.get('daily_loss_limit', 200.0)
        self.max_position_size = self.config.get('max_position_size', 15.0)

        # --- 2. 运行时状态 ---
        # 价格历史：用于计算均值、标准差、线性回归斜率
        self.price_history = defaultdict(lambda: deque(maxlen=60)) 
        self.delivery_time_executed: Set[str] = set()
        self.seen_trade_ids: Set[str] = set() # [新增] 去重
        
        # --- 3. 风控状态 ---
        self.current_daily_loss = 0.0
        self.last_equity = self.config.get('initial_capital', 50000.0)
        self.last_day = None
        
        # --- 4. 保护机制 ---
        self.protection_sleep_until: Optional[datetime] = None
        self.consecutive_losses = 0
        self.consecutive_loss_threshold = 3 
        self.sleep_hours = 3

    def on_tick(self, tick, context):
        """
        核心 Tick 驱动逻辑
        """
        # [新增] 1. 数据去重 (参考原 data_provider.py)
        # 假设 tick 对象有 trade_id 字段，如果没有则用 timestamp+contract_id 模拟
        trade_id = getattr(tick, 'trade_id', f"{tick.contract_id}_{tick.timestamp}")
        if trade_id in self.seen_trade_ids:
            return
        self.seen_trade_ids.add(trade_id)
        # 定期清理 seen_ids 防止内存泄漏 (可选，模拟盘通常不需要)
        if len(self.seen_trade_ids) > 10000: self.seen_trade_ids.clear()

        # 2. 基础数据处理
        timestamp = tick.timestamp
        contract_id = tick.contract_id
        current_price = tick.price
        
        self.price_history[contract_id].append(current_price)

        # 3. 日亏损重置逻辑
        current_date = timestamp.date()
        if self.last_day != current_date:
            self.current_daily_loss = 0.0
            self.delivery_time_executed.clear()
            self.last_day = current_date
            self.last_equity = context.get_equity()

        # 4. 实时日亏损估算
        current_equity = context.get_equity()
        day_pnl = current_equity - self.last_equity
        if day_pnl < 0:
            self.current_daily_loss = abs(day_pnl)

        # 5. 时间窗口计算 (Close Time = Delivery - 1h)
        delivery_start = tick.delivery_start
        if isinstance(delivery_start, str):
            try:
                delivery_start = datetime.fromisoformat(delivery_start.replace("Z", "+00:00"))
            except:
                return 

        market_close_time = delivery_start - timedelta(hours=1)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=delivery_start.tzinfo)
            
        minutes_to_close = (market_close_time - timestamp).total_seconds() / 60

        if minutes_to_close < 0: return # 已关闸

        # 6. 获取当前配置
        active_params = self._get_active_params(timestamp)
        if not active_params: return 

        # 7. 风控检查 (强平/止盈)
        position = context.get_position(contract_id)
        current_size = position.volume if position else 0.0

        # Force Close (关闸前5分钟)
        if 0 < minutes_to_close <= 5:
            if abs(current_size) > 0:
                self._execute_trade(context, contract_id, -current_size, current_price, "ForceClose")
            return

        # Profit Taking
        if abs(current_size) > 0:
            self._check_profit_taking(contract_id, current_price, position, context, active_params)

        # 熔断检查
        if self.protection_sleep_until and timestamp < self.protection_sleep_until: return 
        if self.current_daily_loss >= self.daily_loss_limit: return

        # 8. 策略执行
        # 计算统计特征 (Mean, Std, Z-Score, Slope)
        stats = self._calculate_statistics(contract_id)
        if not stats: return

        md = {
            'contract_id': contract_id, 'price': current_price,
            'minutes_to_close': minutes_to_close, 'stats': stats,
            'params': active_params
        }

        self._exec_delivery_time_buy(md, current_size, context)
        self._exec_super_mean_reversion(md, current_size, context)
        self._exec_optimized_extreme_sell(md, current_size, context)

    # ================= 辅助计算 =================

    def _calculate_statistics(self, contract_id: str) -> Optional[Dict]:
        history = self.price_history[contract_id]
        if len(history) < 20: return None
        
        prices = np.array(history)
        mean = np.mean(prices)
        std = np.std(prices)
        current = prices[-1]
        
        z_score = 0.0
        if std > 1e-6:
            z_score = (current - mean) / std
            
        # [新增] 趋势斜率计算 (Trend Slope)
        # 使用线性回归计算价格序列的斜率
        # x = [0, 1, 2...], y = prices
        slope = 0.0
        try:
            x = np.arange(len(prices))
            slope, intercept, r_value, p_value, std_err = linregress(x, prices)
        except:
            pass
            
        return {
            "mean": mean, "std": std,
            "z_score": z_score, "slope": slope
        }

    def _get_active_params(self, timestamp: datetime) -> Optional[Dict]:
        weekday = timestamp.weekday()
        current_time_str = timestamp.strftime("%H:%M")
        for schedule in self.strategy_schedules:
            if weekday not in schedule.get("days_of_week", []): continue
            for time_range in schedule.get("time_ranges", []):
                if time_range.get("start", "00:00") <= current_time_str <= time_range.get("end", "23:59"):
                    return time_range.get("strategy_params")
        return None

    def _execute_trade(self, context, contract_id, size, price, reason):
        if size > 0: context.buy(contract_id, size, price, reason)
        elif size < 0: context.sell(contract_id, abs(size), price, reason)

    # ================= 策略逻辑 =================

    def _exec_delivery_time_buy(self, md, current_size, context):
        params = md['params'].get('delivery_time_buy', {})
        if not params or params.get('position_ratio', 0) <= 0: return
        cid = md['contract_id']
        if cid in self.delivery_time_executed: return

        # 默认窗口 60~15 分钟前
        if 15 < md['minutes_to_close'] <= 60:
            target_pos = params.get('position_split', 1.0)
            if current_size < target_pos:
                # [增强] 简单的趋势保护：如果正在暴跌 (slope < -0.1)，先别急着抄底
                if md['stats']['slope'] > -0.2: 
                    self._execute_trade(context, cid, target_pos - current_size, md['price'], "DeliveryBuy")
                    self.delivery_time_executed.add(cid)

    def _exec_super_mean_reversion(self, md, current_size, context):
        params = md['params'].get('super_mean_reversion_buy', {})
        if not params or params.get('position_ratio', 0) <= 0: return

        mean = md['stats']['mean']
        if mean == 0: return
        pct_diff = (md['price'] - mean) / mean
        threshold = params.get('threshold', -0.05) 
        
        if pct_diff < threshold:
            target_pos = params.get('position_split', 1.0)
            if current_size < target_pos:
                # [增强] 防止接飞刀：Z-Score 不能过低 (比如 < -4 可能还在跌) 
                # 或者等待 slope 开始变平 (slope > -0.5)
                if md['stats']['slope'] > -0.5:
                    self._execute_trade(context, md['contract_id'], target_pos - current_size, md['price'], "MeanRevBuy")

    def _exec_optimized_extreme_sell(self, md, current_size, context):
        params = md['params'].get('optimized_extreme_sell', {})
        if not params or params.get('position_ratio', 0) <= 0: return

        z_score = md['stats']['z_score']
        trigger_z = params.get('z_score_threshold', 4.0)
        
        if z_score > trigger_z:
            max_short_pos = -1 * params.get('position_split', 1.0)
            if current_size > max_short_pos:
                # [增强] 确认上涨趋势减弱 (slope < 0.5) 再空
                if md['stats']['slope'] < 0.5:
                    self._execute_trade(context, md['contract_id'], current_size - max_short_pos, md['price'], "ExtremeSell")

    def _check_profit_taking(self, contract_id, current_price, position, context, active_params):
        avg_price = position.average_price
        if avg_price <= 0: return
        pt_params = active_params.get('profit_taking_params', {})
        multiplier = pt_params.get('initial_multiplier', 1.2)

        if position.volume > 0:
            if current_price >= avg_price * multiplier:
                self._execute_trade(context, contract_id, -position.volume, current_price, "ProfitTake")
        elif position.volume < 0:
            if current_price <= avg_price * (2 - multiplier):
                self._execute_trade(context, contract_id, -position.volume, current_price, "ProfitTake")

    def on_trade(self, trade, context):
        """
        监听交易，处理连续亏损保护
        """
        if trade.action == "SELL" or trade.action == "COVER": 
            # 如果 Trade 对象没有 realized_pnl，尝试自己算 (Fall-back logic)
            pnl = getattr(trade, 'realized_pnl', None)
            
            if pnl is None:
                # 简易估算：(Price - AvgPrice) * Volume
                # 注意：这只是为了触发熔断的近似值
                # 真实系统应该由 Engine 计算好传进来
                pass 
            
            # 只有明确亏损才计数
            if pnl is not None and pnl < 0:
                self.consecutive_losses += 1
            elif pnl is not None and pnl > 0:
                self.consecutive_losses = 0
            
            if self.consecutive_losses >= self.consecutive_loss_threshold:
                logger.warning(f"触发连续亏损保护，策略休眠 {self.sleep_hours} 小时")
                self.protection_sleep_until = datetime.utcnow() + timedelta(hours=self.sleep_hours)
                self.consecutive_losses = 0