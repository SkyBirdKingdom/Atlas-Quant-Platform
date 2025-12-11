import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
import pandas as pd
import pandas_ta as ta
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..utils.time_helper import get_trading_window

# === 1. 定义我们支持的标准指标集 ===
# 这是这一阶段的核心配置，决定了你的 AI/策略能“看到”什么
# 0.3.14b 版本支持 ta.Strategy 语法
StandardStrategy = ta.Strategy(
    name="NordPool_Advanced_Indicators",
    ta=[
        # 1. 震荡/反转类 (Mean Reversion)
        {"kind": "rsi", "length": 14},  # RSI_14
        {"kind": "cci", "length": 20},  # CCI_20_0.015
        
        # 2. 趋势类 (Trend)
        {"kind": "macd", "fast": 12, "slow": 26, "signal": 9},
        {"kind": "sma", "length": 20},  # SMA_20 (短期趋势)
        {"kind": "sma", "length": 50},  # SMA_50 (中期趋势)
        {"kind": "sma", "length": 200}, # SMA_200 (牛熊分界线)

        # 3. 波动率类 (Volatility)
        # 布林带会生成 BBL_20_2.0 (下轨), BBU_20_2.0 (上轨), BBM_20_2.0 (中轨)
        {"kind": "bbands", "length": 20, "std": 2}, 
        {"kind": "atr", "length": 14},  # ATRr_14
    ]
)

def get_contract_features(db: Session, contract_id: str, area: str):
    """
    【严谨版】获取特定区域、特定合约的 K 线
    逻辑：
    1. 查出合约的 delivery_start (为了计算交易窗口)
    2. 计算 open_time 和 close_time
    3. 严格提取该窗口内的 K 线
    """
    
    # 1. 先查合约的 Delivery Start
    # 我们从 trades 表或者 market_candles 表反查都可以，这里查 trades 更保险
    # 注意：这里假设 contract_id 在该 area 下唯一对应的 delivery_start 是一致的
    meta_query = text("""
        SELECT delivery_start 
        FROM trades 
        WHERE delivery_area = :area AND contract_id = :cid
        LIMIT 1
    """)
    meta = db.execute(meta_query, {"area": area, "cid": contract_id}).fetchone()
    
    if not meta:
        return pd.DataFrame()
        
    delivery_start_utc = meta[0]
    
    # 2. 计算交易窗口 (D-1 13:00 到 Delivery-1h)
    open_ts, close_ts = get_trading_window(delivery_start_utc)
    
    # 3. 带时间约束的 K 线查询
    query = text("""
        SELECT 
            timestamp, 
            open, high, low, close, volume, 
            contract_type
        FROM market_candles
        WHERE contract_id = :cid 
          AND area = :area
        ORDER BY timestamp ASC
    """)
    
    rows = db.execute(query, {
        "cid": contract_id, 
        "area": area,
        # "open_ts": open_ts,
        # "close_ts": close_ts
    }).fetchall()
    
    if rows:
        print(f"✅ [DEBUG] {contract_id} 成功取到 {len(rows)} 条数据!")
        print(f"   第一条时间: {rows[0][0]} | 类型: {type(rows[0][0])}")
        print(f"   交易窗口应为: {open_ts} -> {close_ts}")
        df = pd.DataFrame(rows)
        df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'type']
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        cols = ['open', 'high', 'low', 'close', 'volume']
        df[cols] = df[cols].astype(float)
    else:
        print(f"❌ [DEBUG] {contract_id} 依然无数据 (已移除时间限制)")
        df = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume', 'type'])

    full_idx = pd.date_range(start=open_ts.replace(tzinfo=None), 
                             end=close_ts.replace(tzinfo=None), 
                             freq='1min', inclusive='left')
    
    df = df.reindex(full_idx)
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].ffill()
    # df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].bfill()
    df['volume'] = df['volume'].fillna(0)
    df['type'] = df['type'].ffill().bfill()
    
    # 4. 计算指标
    try:
        df.ta.strategy(StandardStrategy)
    except Exception:
        pass
    
    if 'RSI_14' not in df.columns:
        return pd.DataFrame()
    
    # 5. 清洗
    # df.dropna(subset=['RSI_14'], inplace=True)
    
    return df

def get_market_features(db: Session, area: str, start_date: str, end_date: str):
    if 'T' not in start_date: start_date += " 00:00:00"
    if 'T' not in end_date: end_date += " 23:59:59"

    # 【修复点 1】在 SELECT 中增加 MAX(contract_type) as type
    query = text("""
        SELECT 
            timestamp, 
            MAX(contract_type) as type, 
            AVG(open) as open, 
            MAX(high) as high, 
            MIN(low) as low, 
            AVG(close) as close, 
            SUM(volume) as volume
        FROM market_candles
        WHERE area = :area 
          AND timestamp >= :start 
          AND timestamp <= :end
        GROUP BY timestamp
        ORDER BY timestamp ASC
    """)
    
    rows = db.execute(query, {"area": area, "start": start_date, "end": end_date}).fetchall()
    
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # 【修复点 2】列名中增加 'type'
    df.columns = ['timestamp', 'type', 'open', 'high', 'low', 'close', 'volume']
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    
    cols = ['open', 'high', 'low', 'close', 'volume']
    df[cols] = df[cols].astype(float)

    # 计算指标
    df.ta.strategy(StandardStrategy)
    df.dropna(inplace=True)
    
    return df

def get_latest_features(db: Session, area: str, lookback=500):
    # 【修复点 3】同样在实盘查询中增加 type
    query = text("""
        SELECT 
            timestamp, 
            MAX(contract_type) as type,
            AVG(open) as open, 
            MAX(high) as high, 
            MIN(low) as low, 
            AVG(close) as close, 
            SUM(volume) as volume
        FROM market_candles
        WHERE area = :area 
        GROUP BY timestamp
        ORDER BY timestamp DESC
        LIMIT :limit
    """)
    rows = db.execute(query, {"area": area, "limit": lookback}).fetchall()
    
    if not rows:
        return pd.DataFrame()
        
    df = pd.DataFrame(rows)
    # 【修复点 4】列名中增加 'type'
    df.columns = ['timestamp', 'type', 'open', 'high', 'low', 'close', 'volume']
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    df = df.iloc[::-1].set_index('timestamp')
    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    
    df.ta.strategy(StandardStrategy)
    
    return df