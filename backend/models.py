from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from .database import Base

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