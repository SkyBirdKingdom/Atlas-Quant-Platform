# backend/strategy/engine.py
import logging
import uuid
from decimal import Decimal, getcontext, ROUND_HALF_UP
from typing import List, Dict, Optional, Any
from datetime import datetime

# 设置金融计算精度
getcontext().prec = 40
logger = logging.getLogger("TradeEngine")

class Order:
    """
    标准订单对象
    支持实盘与回测状态追踪
    """
    def __init__(self, target_pos, type='MARKET', limit_price=None, ttl=60, reason=""):
        self.id = str(uuid.uuid4())
        self.target_pos = self._clean(target_pos)
        self.type = type # 'MARKET', 'LIMIT'
        self.limit_price = self._clean(limit_price) if limit_price is not None else None
        self.ttl = int(ttl) 
        
        # 时间戳记录
        self.created_at_ts = None  # 订单创建时间
        self.updated_at_ts = None  # 最后更新时间
        
        self.reason = reason
        self.status = 'PENDING' # PENDING, FILLED, CANCELED, EXPIRED
        self.filled_vol = Decimal("0")
    
    @staticmethod
    def _clean(val):
        if val is None: return Decimal("0")
        d_val = Decimal(str(val)) if isinstance(val, (float, int)) else Decimal(val)
        return d_val.quantize(Decimal("1.0000000000"), rounding=ROUND_HALF_UP)
    
    def to_dict(self):
        """序列化 (用于存库)"""
        return {
            "id": self.id,
            "target_pos": str(self.target_pos),
            "type": self.type,
            "limit_price": str(self.limit_price) if self.limit_price else None,
            "ttl": self.ttl,
            "created_at_ts": str(self.created_at_ts) if self.created_at_ts else None,
            "reason": self.reason,
            "status": self.status,
            "filled_vol": str(self.filled_vol)
        }

    @classmethod
    def from_dict(cls, d):
        o = cls(d['target_pos'], d['type'], d['limit_price'], d['ttl'], d['reason'])
        o.id = d['id']
        o.created_at_ts = d.get('created_at_ts')
        o.status = d.get('status', 'PENDING')
        o.filled_vol = Decimal(d.get('filled_vol', '0'))
        return o

class TradeEngine:
    """
    【Atlas V2 统一交易引擎】
    事件驱动核心：支持 Tick 流和 Candle 流的双重驱动。
    """
    def __init__(self, mode='PAPER', close_ts=None, force_close_minutes=10, enable_slippage=True, contract_type='PH'):
        """
        :param mode: 'REPLAY' (复盘), 'PAPER' (模拟), 'LIVE' (实盘)
        """
        self.mode = mode
        self.contract_close_ts = close_ts 
        self.force_close_minutes = int(force_close_minutes)
        self.enable_slippage = enable_slippage 
        
        # 费率常量
        self.fee_rate_per_mwh = Decimal("0.23")
        self.duration_hours = Decimal("0.25") if contract_type == 'QH' else Decimal("1.0")

        # === 核心状态 (Decimal) ===
        self.cash = Decimal("40000.0")
        self.current_position = Decimal("0.0")
        self.active_orders: List[Order] = [] 
        
        # === 统计状态 ===
        self.total_slippage_cost = Decimal("0.0")
        self.total_fee_cost = Decimal("0.0")
        self.history = [] # 仅在 REPLAY 模式下记录完整历史
        
        # === 临时状态 (单步快照) ===
        self.last_price = Decimal("0.0")
        self.current_time = None

    def clean_decimal(self, val):
        if val is None: return Decimal("0")
        d_val = Decimal(str(val)) if isinstance(val, (float, int)) else Decimal(val)
        return d_val.quantize(Decimal("1.0000000000"), rounding=ROUND_HALF_UP)

    # --- 状态管理 (Load/Save) ---
    def get_state(self):
        return {
            "cash": str(self.cash),
            "position": str(self.current_position),
            "orders": [o.to_dict() for o in self.active_orders],
            "stats": {
                "slippage": str(self.total_slippage_cost),
                "fees": str(self.total_fee_cost)
            }
        }

    def restore_state(self, state: Dict):
        if not state: return
        self.cash = Decimal(state.get("cash", "0"))
        self.current_position = Decimal(state.get("position", "0"))
        self.active_orders = [Order.from_dict(o) for o in state.get("orders", [])]
        stats = state.get("stats", {})
        self.total_slippage_cost = Decimal(stats.get("slippage", "0"))
        self.total_fee_cost = Decimal(stats.get("fees", "0"))

    # --- 事件驱动接口 (Event Handlers) ---
    
    def update_tick(self, tick, strategy):
        """
        【Tick 驱动】处理逐笔成交/盘口变化
        :param tick: OrderFlowTick 对象
        """
        self.current_time = tick.timestamp
        price = self.clean_decimal(tick.price)
        self.last_price = price
        
        # 1. 撮合 (仅在非实盘模式下)
        if self.mode != 'LIVE':
            self._match_tick(tick)
            
        # 2. 强平检查
        if self._check_force_close(self.current_time):
            self._force_close_all(price, "FORCE_CLOSE_TICK")
            return

        # 3. 策略回调
        strategy.on_tick(tick)

    def update_candle(self, candle: Dict, strategy):
        """
        【Candle 驱动】处理 K 线更新 (用于旧策略或低频策略)
        """
        ts = candle.get('time') or candle.get('timestamp')
        self.current_time = ts
        close_price = self.clean_decimal(candle.get('close'))
        self.last_price = close_price
        
        # 1. 撮合 (基于 OHLC 的粗粒度撮合)
        if self.mode != 'LIVE':
            self._match_candle(candle)
            
        # 2. 强平检查
        if self._check_force_close(ts):
            self._force_close_all(close_price, "FORCE_CLOSE_CANDLE")
            return

        # 3. 策略回调
        strategy.on_candle(candle)
        
        # 4. 记录历史 (仅复盘模式)
        if self.mode == 'REPLAY':
            self._record_history(candle)

    # --- 交易操作 (Actions) ---

    def execute_order(self, target_pos, reason=""):
        """策略层调用的标准下单接口 (Wrapper)"""
        self.place_order(target_pos, type='MARKET', reason=reason)

    def place_order(self, target_pos, type='MARKET', limit_price=None, reason="", ttl=60):
        target = self.clean_decimal(target_pos)
        
        # 幂等性检查：如果已有相同目标的挂单，忽略
        for o in self.active_orders:
            if o.target_pos == target and o.type == type:
                return

        order = Order(target, type, limit_price, ttl, reason)
        order.created_at_ts = self.current_time
        
        self.active_orders.append(order)
        
        if self.mode == 'LIVE':
            # TODO: Phase 3 对接真实 API 下单
            logger.info(f"⚡ [LIVE] 发送实盘订单: {target} @ {type}")
        else:
            logger.info(f"📝 [SIM] 本地挂单: {target} @ {type} ({reason})")

    # --- 内部核心逻辑 (Internals) ---

    def _check_force_close(self, current_ts):
        """检查是否到达强平时间"""
        if not self.contract_close_ts or not current_ts:
            return False
        # 简单转换：确保都是 datetime
        # 注意：这里需要严谨的时区处理，Phase 1 假设都是 UTC naive
        if hasattr(current_ts, 'to_pydatetime'): current_ts = current_ts.to_pydatetime()
        
        from datetime import timedelta
        deadline = self.contract_close_ts - timedelta(minutes=self.force_close_minutes)
        return current_ts >= deadline

    def _force_close_all(self, price, reason):
        """强平所有持仓"""
        if abs(self.current_position) > Decimal("1e-6"):
            vol = abs(self.current_position)
            is_buy = self.current_position < 0
            self._execute_trade(vol, price, is_buy, reason)
            self.current_position = Decimal("0")
        self.active_orders = [] # 撤销所有挂单

    def _match_tick(self, tick):
        """
        【Tick 级微观撮合】
        逻辑：如果是 TRADE 类型 Tick，且价格穿过 Limit 单，则成交
        """
        if not self.active_orders: return
        if tick.type != 'TRADE': return # 只有市场有成交，我们才撮合 (防止虚假流动性)

        tick_price = self.clean_decimal(tick.price)
        tick_vol = self.clean_decimal(tick.volume) # 这是市场的真实成交量
        
        # 简单撮合逻辑：只要价格合适，假设我们能吃到这笔流
        # 进阶逻辑：需要 OrderBook 重建，计算排队位置 (Phase 3)
        remaining_orders = []
        
        for order in self.active_orders:
            exec_price = None
            is_buy = order.target_pos > self.current_position
            
            # 市价单：遇到成交 Tick 就吃
            if order.type == 'MARKET':
                exec_price = tick_price
            # 限价单：价格穿过才成交
            elif order.type == 'LIMIT':
                limit = order.limit_price
                if is_buy and tick_price <= limit:
                    exec_price = limit # 买入：市场价低，按限价成交(或更优)
                elif not is_buy and tick_price >= limit:
                    exec_price = limit
            
            if exec_price:
                # 能够成交
                needed = abs(order.target_pos - self.current_position)
                # 限制：不能超过市场这笔 Tick 的量 (真实流动性约束)
                trade_vol = min(needed, tick_vol)
                
                if trade_vol > 0:
                    self._execute_trade(trade_vol, exec_price, is_buy, order.reason)
                    if abs(order.target_pos - self.current_position) > Decimal("1e-6"):
                        remaining_orders.append(order) # 没吃饱，继续挂
                else:
                    remaining_orders.append(order)
            else:
                remaining_orders.append(order)
                
        self.active_orders = remaining_orders

    def _match_candle(self, candle):
        """
        【Candle 级宏观撮合】
        逻辑：利用 OHLC 进行大概率撮合
        """
        if not self.active_orders: return
        
        open_p = self.clean_decimal(candle.get('open'))
        high_p = self.clean_decimal(candle.get('high'))
        low_p = self.clean_decimal(candle.get('low'))
        vol = self.clean_decimal(candle.get('volume'))
        
        if vol <= 0: return
        
        remaining = []
        available = vol
        
        for order in self.active_orders:
            if available <= 0: 
                remaining.append(order)
                continue
                
            exec_price = None
            is_buy = order.target_pos > self.current_position
            
            if order.type == 'MARKET':
                exec_price = open_p
            elif order.type == 'LIMIT':
                limit = order.limit_price
                # 检查 K 线最高最低价是否触及限价
                if is_buy and low_p <= limit:
                    exec_price = min(open_p, limit) if open_p < limit else limit
                elif not is_buy and high_p >= limit:
                    exec_price = max(open_p, limit) if open_p > limit else limit
            
            if exec_price:
                needed = abs(order.target_pos - self.current_position)
                # 简单假设：这根 K 线内最多能吃掉全部量 (回测妥协)
                trade_vol = min(needed, available)
                
                self._execute_trade(trade_vol, exec_price, is_buy, order.reason)
                available -= trade_vol
                
                if abs(order.target_pos - self.current_position) > Decimal("1e-6"):
                    remaining.append(order)
            else:
                remaining.append(order)
        
        self.active_orders = remaining

    def _execute_trade(self, vol, price, is_buy, reason):
        """核心记账与扣费"""
        val = vol * price
        
        # 1. 资金变动
        if is_buy:
            self.cash -= val
            self.current_position += vol
        else:
            self.cash += val
            self.current_position -= vol
            
        # 2. 费用 (Fee)
        fee = vol * self.duration_hours * self.fee_rate_per_mwh
        self.cash -= fee
        self.total_fee_cost += fee
        
        # 3. 滑点 (Slippage)
        slip = Decimal("0")
        if self.enable_slippage:
            # 基础 2bps + 冲击成本
            rate = Decimal("0.0002") * (Decimal("1.0") + (vol / Decimal("10.0")) * Decimal("0.5"))
            slip = val * rate
            self.cash -= slip
            self.total_slippage_cost += slip
            
        logger.info(f"💰 [TRADE] {'BUY' if is_buy else 'SELL'} {vol} @ {price} | Fee: {fee:.2f} | Slip: {slip:.2f}")

    def _record_history(self, candle):
        """记录历史快照 (用于前端回放)"""
        ts = candle.get('time') or candle.get('timestamp')
        equity = self.cash + (self.current_position * self.last_price)
        
        self.history.append({
            "time": ts,
            "open": candle.get('open'),
            "close": candle.get('close'), # ... 简化，实际应存完整
            "position": float(self.current_position),
            "cash": float(self.cash),
            "equity": float(equity),
            "slippage": float(self.total_slippage_cost),
            "fees": float(self.total_fee_cost)
        })
    
    def get_results(self):
        """兼容旧版接口"""
        return {
            "history": self.history,
            "total_slippage": float(self.total_slippage_cost),
            "total_fees": float(self.total_fee_cost)
        }