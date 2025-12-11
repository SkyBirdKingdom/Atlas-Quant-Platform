import pandas as pd
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from ..models import MarketCandle

logger = logging.getLogger("KlineGen")

def generate_1min_candles_chunk(db: Session, area: str, chunk_start: datetime, chunk_end: datetime):
    """
    处理单天的数据生成 (内部函数)
    """
    # 1. 查询原始 Tick 数据
    query = text("""
        SELECT 
            contract_id,
            contract_type,
            trade_time,
            price,
            volume
        FROM trades
        WHERE delivery_area = :area
          AND trade_time >= :start
          AND trade_time < :end
        ORDER BY trade_time ASC
    """)
    
    result = db.execute(query, {
        "area": area, 
        "start": chunk_start, 
        "end": chunk_end
    }).fetchall()
    
    if not result:
        return 0 

    # 2. 转 Pandas
    df = pd.DataFrame(result)
    df.columns = ['contract_id', 'contract_type', 'timestamp', 'price', 'volume']
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    candles = []
    grouped = df.groupby('contract_id')
    
    for c_id, group in grouped:
        c_type = group['contract_type'].iloc[0]
        group = group.set_index('timestamp')
        
        # 3. 重采样为 1min
        ohlc = group['price'].resample('1min').ohlc()
        vol = group['volume'].resample('1min').sum()
        count = group['price'].resample('1min').count()
        vwap_val = (group['price'] * group['volume']).resample('1min').sum() / vol
        
        # 合并
        kline_df = pd.concat([ohlc, vol, count, vwap_val], axis=1)
        kline_df.columns = ['open', 'high', 'low', 'close', 'volume', 'trade_count', 'vwap']
        
        # 4. 只保留有成交的分钟
        kline_df = kline_df[kline_df['volume'] > 0].dropna()
        
        for ts, row in kline_df.iterrows():
            # === 关键修复点 ===
            # 必须使用 float() 和 int() 将 numpy 类型强制转换为 python 原生类型
            # 否则 PostgreSQL 会报 'schema np does not exist' 错误
            candles.append({
                "contract_id": c_id,
                "timestamp": ts,
                "area": area,
                "contract_type": c_type,
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row['volume']),
                "vwap": float(row['vwap']),
                "trade_count": int(row['trade_count'])
            })

    # 5. 批量入库
    if candles:
        batch_size = 5000
        for i in range(0, len(candles), batch_size):
            batch = candles[i : i + batch_size]
            stmt = insert(MarketCandle).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=['contract_id', 'timestamp', 'area'],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                    "vwap": stmt.excluded.vwap,
                    "trade_count": stmt.excluded.trade_count
                }
            )
            db.execute(stmt)
        db.commit()
    
    return len(candles)

def generate_1min_candles(db: Session, area: str, start_date: str, end_date: str):
    """
    主入口
    """
    # 兼容 ISO 格式的时间字符串处理
    if 'T' in start_date:
        start_dt = datetime.fromisoformat(start_date.replace('Z', ''))
    else:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")

    if 'T' in end_date:
        end_dt = datetime.fromisoformat(end_date.replace('Z', ''))
    else:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    
    logger.info(f"🚀 开始生成 {area} K线: {start_dt} -> {end_dt}")
    
    current = start_dt
    total_generated = 0
    
    while current < end_dt:
        chunk_end = min(current + timedelta(days=1), end_dt)
        
        logger.info(f"⏳ 处理切片: {current.strftime('%Y-%m-%d %H:%M')} -> {chunk_end.strftime('%H:%M')} ...")
        # 记录一下开始时间，用于计算耗时
        t0 = datetime.now()

        count = generate_1min_candles_chunk(db, area, current, chunk_end)

        cost = (datetime.now() - t0).total_seconds()
        # 【新增】打印耗时和生成数量
        logger.info(f"   ✅ 切片完成: 生成 {count} 条 K线 (耗时 {cost:.2f}s)")
        
        total_generated += count
        
        current = chunk_end
        
    logger.info(f"✅ {area} K线生成完毕，共生成 {total_generated} 条 Bar 数据")