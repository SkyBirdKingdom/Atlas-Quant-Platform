# backend/services/kline_generator.py
import logging
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
from ..models import MarketCandle

logger = logging.getLogger("KlineGen")

def generate_1min_candles(db: Session, area: str, start_time: str, end_time: str) -> int:
    """
    生成 1分钟 K 线 (按 contract_id 独立生成)
    
    逻辑变更：
    以前：GROUP BY timestamp (所有合约混在一起)
    现在：GROUP BY timestamp, contract_id (每个合约独立生成 K 线)
    """
    
    # 使用 date_trunc 将时间规整到分钟
    # 注意：Nord Pool 的 trade_time 是 UTC
    
    query = text("""
        INSERT INTO market_candles (
            contract_id, timestamp, area, contract_type,
            open, high, low, close, volume, vwap, trade_count
        )
        SELECT 
            contract_id,
            date_trunc('minute', trade_time) as minute_ts,
            :area,
            contract_type,
            
            -- Open: 该分钟第一笔成交价
            (array_agg(price ORDER BY trade_time ASC))[1] as open_price,
            
            -- High: 该分钟最高价
            MAX(price) as high_price,
            
            -- Low: 该分钟最低价
            MIN(price) as low_price,
            
            -- Close: 该分钟最后一笔成交价
            (array_agg(price ORDER BY trade_time DESC))[1] as close_price,
            
            -- Volume: 该分钟总成交量
            SUM(volume) as total_vol,
            
            -- VWAP: 成交量加权平均价 (Sum(P*V) / Sum(V))
            SUM(price * volume) / SUM(volume) as vwap_price,
            
            -- Count: 笔数
            COUNT(*) as trade_cnt
            
        FROM trades
        WHERE delivery_area = :area
          AND trade_time >= :start
          AND trade_time < :end
        GROUP BY minute_ts, contract_id, contract_type
        
        ON CONFLICT (contract_id, timestamp, area) 
        DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            vwap = EXCLUDED.vwap,
            trade_count = EXCLUDED.trade_count;
    """)
    
    try:
        result = db.execute(query, {
            "area": area,
            "start": start_time,
            "end": end_time
        })
        db.commit()
        
        # result.rowcount 在某些 DB 驱动下可能不准确，但在 SQLAlchemy + Postgres 通常可用
        # 这里返回的是“受影响行数”，即生成的 K 线数量
        return result.rowcount
        
    except Exception as e:
        logger.error(f"Generate K-line failed: {e}")
        db.rollback()
        return 0