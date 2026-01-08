from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON, Boolean, Index, UniqueConstraint, BigInteger
from .database import Base
from datetime import datetime

class Trade(Base):
    __tablename__ = "trades"

    # --- 复合主键设计 ---
    # 组合这三个字段作为主键，可以完美区分同一笔交易在不同区域、不同方向的记录
    trade_id = Column(String, primary_key=True, index=True)
    delivery_area = Column(String, primary_key=True, index=True) # SE3, SE2...
    trade_side = Column(String, primary_key=True, index=True)    # Buy, Sell

    # --- 合约基础信息 ---
    contract_id = Column(String, index=True)
    contract_name = Column(String, index=True)
    delivery_start = Column(DateTime, index=True)
    delivery_end = Column(DateTime, index=True)
    duration_minutes = Column(Float)
    contract_type = Column(String, index=True) # PH, QH, Other

    # --- 交易详细信息 ---
    price = Column(Float)
    volume = Column(Float)
    trade_time = Column(DateTime, index=True)       # tradeTime
    trade_updated_at = Column(DateTime, nullable=True) # tradeUpdatedAt
    
    # 状态与版本
    trade_state = Column(String, nullable=True)    # Completed, Cancelled, Disputed
    revision_number = Column(Integer, nullable=True)
    trade_phase = Column(String, nullable=True)    # Continuous, Auction
    cross_px = Column(Boolean, nullable=True)      # 是否跨交易所

    # --- Leg (订单腿) 详细信息 ---
    # referenceOrderId 可能为空（当跨交易所时，另一条腿可能没有ID）
    reference_order_id = Column(String, nullable=True, index=True) 

    # 辅助字段
    created_at = Column(DateTime, nullable=True) # 记录入库时间

class FetchState(Base):
    __tablename__ = "fetch_state"
    area = Column(String, primary_key=True)
    last_fetched_time = Column(DateTime)
    updated_at = Column(DateTime)

    # 新增：记录任务状态和错误信息
    status = Column(String, default="idle")  # 'running', 'error', 'ok'
    last_error = Column(Text, nullable=True) # 具体的报错信息

class KlineGenState(Base):
    """
    【新增】K线生成进度表
    替代原本通过 MAX(timestamp) 推断进度的方式，彻底消除 Gap Candle
    """
    __tablename__ = "kline_gen_state"
    area = Column(String, primary_key=True)
    last_generated_time = Column(DateTime) # 记录已经处理到的时间点
    updated_at = Column(DateTime, default=datetime.utcnow)

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
    【核心表】订单流全量记录表
    存储 Nord Pool 返回的每一个 Revision 中的 Order 详情。
    无论是 Snapshot 里的订单，还是 Delta 里的变更，都作为一行记录存储。
    """
    __tablename__ = "order_flow_ticks"

    # --- 基础索引信息 ---
    # 建议使用纳秒级时间戳或自增 ID 作为主键，防止高频交易主键冲突
    id = Column(Integer, primary_key=True, autoincrement=True) 
    
    contract_id = Column(String, index=True, nullable=False)
    delivery_area = Column(String, index=True, nullable=False)
    
    # --- 核心时间戳 ---
    # UpdatedTime: 事件发生的时间（API 中的 updatedTime），用于回放
    timestamp = Column(DateTime, index=True, nullable=False)
    # PriorityTime: 撮合优先级时间（非常重要！决定谁先成交）
    priority_time = Column(DateTime, nullable=True)
    
    # --- 订单详情 ---
    order_id = Column(String, index=True, nullable=False)
    # Revision Number: 订单的版本号，用于判断消息顺序
    revision_number = Column(Integer, nullable=False)
    
    price = Column(Float)
    volume = Column(Float) # 当前剩余量
    
    # --- 状态标识 ---
    side = Column(String)   # 'Buy' / 'Sell'
    state = Column(String)  # 'Active', 'Inactive', 'Hibernated'
    action = Column(String) # 'UserAdded', 'UserModified', 'FullExecution'...
    
    # --- 关键重构字段 ---
    # is_snapshot: 标记这条记录是否来自一次全量快照 (isSnapshot=True)
    is_snapshot = Column(Boolean, default=False, index=True)
    
    # deleted: 标记订单是否被删除 (API 显式返回 deleted: true 或 implicit 逻辑)
    is_deleted = Column(Boolean, default=False)

    # --- 辅助字段 ---
    # 用于记录这笔数据是来自实时流(Stream)还是历史归档(Archive)
    source_type = Column(String, default="Stream") 
    created_at = Column(DateTime)

    # --- 联合索引与约束 ---
    __table_args__ = (
        # 核心查询索引：根据合约和时间快速拉取数据流
        Index('idx_orderflow_replay', 'contract_id', 'timestamp', 'revision_number'),
        
        # 业务唯一约束：防止重复消费
        # 注意：同一个订单在同一个 revision 可能出现多次吗？通常不会。
        # 加上 is_snapshot 维度，防止 snapshot 和 delta 冲突（虽然逻辑上它们是不同的事件）
        UniqueConstraint('contract_id', 'order_id', 'revision_number', 'action', name='uq_tick_entry'),
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