from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON, Boolean, Index, UniqueConstraint, BigInteger, Date
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

class OrderContract(Base):
    """
    【新增】合约基础信息表
    对应接口: /api/v2/Intraday/OrderBook/ContractsIds/ByArea
    存储合约的静态属性，如交割时间、开闭市时间等。
    """
    __tablename__ = "order_contracts"

    contract_id = Column(String, primary_key=True, index=True)
    delivery_area = Column(String, primary_key=True, index=True)
    contract_name = Column(String, index=True)
    
    # 日期信息
    delivery_date_utc = Column(Date, index=True) # 方便按天查询
    
    # 时间窗口
    delivery_start = Column(DateTime)
    delivery_end = Column(DateTime)
    contract_open_time = Column(DateTime)
    contract_close_time = Column(DateTime)
    
    # 属性
    is_local_contract = Column(Boolean)
    
    # 从 OrderBook 接口补充的信息 (可能在不同接口中获取)
    volume_unit = Column(String, nullable=True) # MW
    price_unit = Column(String, nullable=True)  # EUR/MWh

    # 【新增字段】标记该合约的历史数据是否已成功归档 (存储为 Parquet 或 DB)
    is_archived = Column(Boolean, default=False, index=True)
    
    updated_at = Column(DateTime, default=datetime.utcnow)

class OrderFlowTick(Base):
    """
    【重构】订单流全量明细表
    对应接口: /api/v2/Intraday/OrderBook/ByContractId (Revisions)
    记录每一个订单的每一次变更 (Create/Update/Delete)。
    """
    __tablename__ = "order_flow_ticks"

    # 1. 主键改为 String 类型的 tick_id，用于存储确定性哈希
    # 解决了 "tick_id is an invalid keyword argument" 错误
    tick_id = Column(String, primary_key=True, index=True) 
    
    # 2. 核心关联
    contract_id = Column(String, index=True, nullable=False)
    delivery_area = Column(String, index=True)
    
    # 3. 版本控制 (API: revision)
    revision_number = Column(Integer, nullable=False, index=True)
    is_snapshot = Column(Boolean, default=False, index=True)
    
    # 4. 订单详情 (API: buyOrders/sellOrders items)
    order_id = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False) # 'BUY' / 'SELL'
    
    price = Column(Float)
    volume = Column(Float)
    
    # 5. 关键时间戳
    # updated_time: 订单最后一次更新的时间 (API: updatedTime)
    updated_time = Column(DateTime, index=True, nullable=False)
    # priority_time: 撮合优先级时间 (API: priorityTime)，同价格排队依据
    priority_time = Column(DateTime, nullable=True)
    
    # 6. 状态
    # API 显式返回 deleted: true/false
    is_deleted = Column(Boolean, default=False)
    
    # 7. 辅助信息
    # 记录该条记录来自哪个 API 响应的 updatedAt (Root Level)，用于版本溯源
    root_updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow) # 入库时间

    # 联合索引：加速回放查询 (按合约+版本号+时间排序)
    __table_args__ = (
        Index('idx_orderflow_replay_v2', 'contract_id', 'revision_number', 'updated_time'),
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