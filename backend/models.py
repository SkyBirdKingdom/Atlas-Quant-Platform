from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON, Boolean, Index, UniqueConstraint
from .database import Base
from datetime import datetime

class Trade(Base):
    __tablename__ = "trades"

    trade_id = Column(String, primary_key=True, index=True)
    
    # --- 新增的关键合约信息 ---
    contract_id = Column(String, index=True)   # 例如: NX_123456
    contract_name = Column(String, index=True) # 例如: PH-20251201-01
    
    price = Column(Float)
    volume = Column(Float)
    delivery_area = Column(String, index=True)
    
    delivery_start = Column(DateTime, index=True)
    delivery_end = Column(DateTime, index=True)
    trade_time = Column(DateTime, index=True)
    
    duration_minutes = Column(Float)
    contract_type = Column(String, index=True)

class FetchState(Base):
    __tablename__ = "fetch_state"
    area = Column(String, primary_key=True)
    last_fetched_time = Column(DateTime)
    updated_at = Column(DateTime)

    # 新增：记录任务状态和错误信息
    status = Column(String, default="idle")  # 'running', 'error', 'ok'
    last_error = Column(Text, nullable=True) # 具体的报错信息

class MarketCandle(Base):
    __tablename__ = "market_candles"

    # 复合主键：合约ID + K线时间
    # 为什么不用 trade_id? 因为这是聚合数据
    contract_id = Column(String, primary_key=True, index=True) 
    timestamp = Column(DateTime, primary_key=True, index=True) # K线开始时间 (UTC)
    
    # 维度信息
    area = Column(String, primary_key=True, index=True) # SE3, SE1...
    contract_type = Column(String)    # PH/QH
    
    # OHLCV 数据
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    vwap = Column(Float)     # 成交量加权均价 (非常重要)
    trade_count = Column(Integer) # 这1分钟内有多少笔成交

class BacktestRecord(Base):
    __tablename__ = "backtest_history"

    id = Column(String, primary_key=True, index=True) # UUID
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # === 输入参数 (Snapshot) ===
    strategy_name = Column(String)
    area = Column(String)
    start_date = Column(String)
    end_date = Column(String)
    # 存储具体的策略参数 (如 rsi_buy, max_pos)，用 JSON 存最灵活
    params = Column(JSON) 
    
    # === 输出结果 (Metrics) ===
    total_pnl = Column(Float)
    sharpe_ratio = Column(Float)
    max_drawdown = Column(Float)
    profit_factor = Column(Float)
    win_rate = Column(Float)
    trade_count = Column(Integer)

    # 【新增】合约摘要列表 (JSON)
    # 存储格式: [{"id": "NX_1", "pnl": 100, "count": 5}, ...]
    # 不存 K 线和详细 Logs，只存结果
    contract_stats = Column(JSON)

class OrderFlowTick(Base):
    """
    【订单流逐笔数据】
    记录每一次挂单、撤单、成交的微观事件。
    数据来源：Nord Pool Orders API (Intraday orders / revisions)
    """
    __tablename__ = "order_flow_ticks"

    tick_id = Column(String, primary_key=True, index=True) # UUID
    contract_id = Column(String, index=True, nullable=False)
    contract_name = Column(String, nullable=True)
    delivery_start = Column(DateTime, nullable=True)
    delivery_end = Column(DateTime, nullable=True)
    delivery_area = Column(String, nullable=True, index=True)
    
    # 事件发生时间 (UpdatedTime from API)
    timestamp = Column(DateTime, index=True, nullable=False)
    
    # 价格与量
    price = Column(Float)
    volume = Column(Float)
    remaining_volume = Column(Float)
    
    # 订单方向: 'BUY' (买单), 'SELL' (卖单)
    side = Column(String)
    
    # 事件类型 (映射 API 的 action 字段)
    # 枚举: 'TRADE' (成交), 'NEW' (挂单), 'CANCEL' (撤单/删除), 'UPDATE' (修改)
    # 对应 API Action: 
    #   TRADE  <- PartialExecution, FullExecution
    #   NEW    <- UserAdded
    #   CANCEL <- UserDeleted, SystemDeleted
    #   UPDATE <- UserModified, SystemModified
    type = Column(String, index=True)
    
    # 原始动作 (保留 API 原文字段，方便 debug)
    raw_action = Column(String)
    
    # 主动方 (Aggressor Side) - 仅对 TRADE 类型有效
    # 'BUY': 主动买入吃单, 'SELL': 主动卖出砸盘, 'NONE': 无法判断
    # 需要在 Fetcher 逻辑中通过对比 OrderBook 推算得出
    aggressor_side = Column(String, nullable=True)
    
    # 关联信息
    order_id = Column(String, index=True)
    revision_number = Column(Integer)

    # 建立联合索引以加速按时间回放
    __table_args__ = (
        Index('idx_orderflow_main', 'delivery_area', 'contract_id', 'timestamp'),
        UniqueConstraint('contract_id', 'delivery_area', 'revision_number', 'order_id', 'raw_action', name='uq_tick_business_key'),
    )

class OrderBookSnapshot(Base):
    """
    【订单簿快照】
    记录某一时刻完整的买卖盘口状态。
    数据来源：Nord Pool Orders API (isSnapshot=true) 或 本地重构计算
    """
    __tablename__ = "order_book_snapshots"

    snapshot_id = Column(String, primary_key=True, index=True) # UUID
    contract_id = Column(String, index=True, nullable=False)

    contract_name = Column(String)
    delivery_area = Column(String, index=True)
    delivery_start = Column(DateTime)
    delivery_end = Column(DateTime)
    
    # 快照时间点
    timestamp = Column(DateTime, index=True, nullable=False)
    
    # 版本号 (对应 API revision)
    revision_number = Column(Integer)
    
    # 买盘列表 [[price, vol], [price, vol], ...] 按价格降序
    bids = Column(JSON)
    
    # 卖盘列表 [[price, vol], [price, vol], ...] 按价格升序
    asks = Column(JSON)
    
    # 辅助字段：是否为 API 原生快照 (还是本地推算的)
    is_native = Column(Boolean, default=False)

    __table_args__ = (
        Index('idx_ob_main', 'delivery_area', 'contract_id', 'timestamp'),
    )

class OrderFlowSyncState(Base):
    """
    【新增】订单流同步状态表
    用于断点续传，记录每个区域的历史数据抓取到了哪个时间点
    """
    __tablename__ = "order_flow_sync_state"
    
    area = Column(String, primary_key=True)
    
    # 历史归档进度 (对应 API A, T+1)
    # 比如: 2025-01-01T00:00:00，表示此时间之前的数据已通过 Revisions 接口完美归档
    last_archived_time = Column(DateTime)
    
    # 实时抓取进度 (对应 API B, T+20min)
    # 比如: 2025-10-20T14:30:00，表示此时间之前的 Intraday Orders 已入库
    last_realtime_time = Column(DateTime)
    
    updated_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="idle")
    last_error = Column(Text, nullable=True)