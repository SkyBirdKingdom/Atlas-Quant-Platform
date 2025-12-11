import pytz
from datetime import datetime, timedelta

# 定义时区：使用 'Europe/Stockholm' 可以自动处理 CET 和 CEST 的切换
NORDIC_TZ = pytz.timezone('Europe/Stockholm')
UTC = pytz.UTC

def get_trading_window(delivery_start_utc: datetime):
    """
    根据交割时间(UTC)，计算该合约的【合法交易窗口】(UTC)
    
    规则:
    - 开盘: D-1 的 13:00 CET/CEST
    - 收盘: DeliveryStart - 1小时
    """
    # 1. 确保输入是带时区的 UTC 时间
    if delivery_start_utc.tzinfo is None:
        delivery_start_utc = UTC.localize(delivery_start_utc)
    
    # 2. 将交割时间转为瑞典时间，用来确定 "D" (Delivery Day)
    delivery_local = delivery_start_utc.astimezone(NORDIC_TZ)
    
    # 3. 计算 "D-1" 的日期
    trade_date_local = delivery_local.date() - timedelta(days=1)
    
    # 4. 构造开盘时间: D-1 13:00:00 (本地时间)
    # pytz 会自动根据日期判断是 CET 还是 CEST
    open_local = NORDIC_TZ.localize(datetime(
        trade_date_local.year, 
        trade_date_local.month, 
        trade_date_local.day, 
        13, 0, 0
    ))
    
    # 5. 计算收盘时间: 交割时间 - 1小时
    close_utc = delivery_start_utc - timedelta(hours=1)
    
    # 6. 转回 UTC 返回
    open_utc = open_local.astimezone(UTC)
    
    return open_utc, close_utc

def is_market_open(current_time_utc: datetime, delivery_start_utc: datetime):
    """判断当前时刻是否处于该合约的可交易状态"""
    if current_time_utc.tzinfo is None:
        current_time_utc = UTC.localize(current_time_utc)
        
    start, end = get_trading_window(delivery_start_utc)
    return start <= current_time_utc < end