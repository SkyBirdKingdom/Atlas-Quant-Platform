import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text

def get_ohlcv_data(db: Session, area: str, start_date: str, end_date: str, freq: str = '1h'):
    """
    将逐笔成交数据重采样为 OHLCV K线数据
    freq: '15min', '30min', '1h', '1d'
    """
    # 1. 查出原始数据 (这里我们按 trade_time 还是 delivery_start 聚合？)
    # 对于日内交易分析，通常关注的是：针对【某个交割时刻】的交易，随着【交易时间】推移的价格变化？
    # 或者：不同【交割时刻】的价格走势？
    
    # 场景 A (趋势分析): 看不同交割时间的价格 (比如看最近一个月的每日均价走势) -> use delivery_start
    # 场景 B (盘口博弈): 看针对同一个交割小时，价格在盘中的变化 -> use trade_time
    
    # 我们先做 场景 A (主流分析)，看一段时间内的电价走势
    
    # 构造结束时间
    if len(end_date) == 10: end_date = f"{end_date} 23:59:59"

    query = text("""
        SELECT 
            delivery_start, 
            price, 
            volume 
        FROM trades
        WHERE delivery_area = :area
          AND delivery_start >= :start
          AND delivery_start <= :end
        ORDER BY delivery_start ASC
    """)
    
    result = db.execute(query, {"area": area, "start": start_date, "end": end_date}).fetchall()
    
    if not result:
        return []

    df = pd.DataFrame(result)
    df.columns = ['time', 'price', 'volume']
    df['time'] = pd.to_datetime(df['time'])
    
    # 设置索引用于重采样
    df.set_index('time', inplace=True)
    
    # Pandas Resample 魔法：生成 OHLCV
    # ohlc() 会自动算 开盘、最高、最低、收盘
    ohlc = df['price'].resample(freq).ohlc()
    vol = df['volume'].resample(freq).sum()
    vwap_num = (df['price'] * df['volume']).resample(freq).sum()
    vwap_denom = vol
    
    # 合并
    final_df = pd.concat([ohlc, vol], axis=1)
    final_df['vwap'] = vwap_num / vwap_denom # 计算 VWAP (成交量加权均价)
    
    # 清洗 NaN (无成交的时段)
    final_df.dropna(inplace=True)
    
    # 格式化输出
    data_list = []
    for time, row in final_df.iterrows():
        data_list.append({
            "time": time.strftime('%Y-%m-%d %H:%M'),
            "open": round(row['open'], 2),
            "close": round(row['close'], 2),
            "low": round(row['low'], 2),
            "high": round(row['high'], 2),
            "volume": round(row['volume'], 1),
            "vwap": round(row['vwap'], 2) if row['volume'] > 0 else row['close']
        })
        
    return data_list