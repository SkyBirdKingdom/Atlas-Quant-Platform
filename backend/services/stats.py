from sqlalchemy.orm import Session
from sqlalchemy import func, text, extract, cast, Date
from datetime import timedelta
from ..models import Trade
import pandas as pd
import re
import logging

logger = logging.getLogger("StatsService")

# 1. 获取数据日历 (查看哪天有数据)
def get_data_calendar(db: Session, area: str):
    # SQL: 按日期分组，统计条数
    # select date(delivery_start), count(*) from trades where area='SE3' group by 1
    query = db.query(
        func.date(Trade.delivery_start).label('date'),
        func.count(Trade.trade_id).label('count')
    ).filter(
        Trade.delivery_area == area
    ).group_by(
        func.date(Trade.delivery_start)
    ).all()
    
    return {str(r.date): r.count for r in query}

# 2. 区间热力图数据 (Date x Hour Matrix)
def get_heatmap_data(db: Session, start_date: str, end_date: str, area: str):
    if len(end_date) == 10:
        real_end_date = f"{end_date} 23:59:59"
    else:
        real_end_date = end_date
    # 我们需要构建一个矩阵：X轴=日期，Y轴=小时，值=总成交量/滑点风险
    query = text("""
        SELECT 
            to_char(delivery_start, 'YYYY-MM-DD') as date_str,
            extract(hour from delivery_start) as hour_num,
            contract_type,
            sum(volume) as total_vol,
            stddev(price) as price_std
        FROM trades
        WHERE delivery_area = :area 
          AND delivery_start >= :start 
          AND delivery_start <= :end
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    """)
    
    result = db.execute(query, {"area": area, "start": start_date, "end": real_end_date}).fetchall()
    
    # 转为列表供前端 ECharts 使用
    # ECharts Heatmap 格式: [x坐标, y坐标, value]
    heatmap_data = []
    for row in result:
        heatmap_data.append({
            "date": row.date_str,
            "hour": int(row.hour_num),
            "type": row.contract_type, # 返回 PH 或 QH
            "volume": round(row.total_vol, 1),
            "volatility": round(row.price_std if row.price_std else 0, 2)
        })
        
    return heatmap_data


def get_contract_volume_trend(
    db: Session, 
    area: str, 
    short_name: str, 
    start_date: str, 
    end_date: str,
    hours_before_close: float = None, # 新增参数 N
    min_points: int = 0               # 新增参数 M
):
    """
    获取某一类合约在指定日期范围内的交易量变化趋势
    支持策略过滤：
    1. hours_before_close (N): 仅统计收盘前 N 小时的交易
    2. min_points (M): 必须满足 M 个分钟有成交后，才开始累计后续成交量
    """
    # 1. 解析短名
    match = re.match(r"^([A-Za-z]+)(\d+)$", short_name.strip())
    if not match:
        raise ValueError("合约简称格式错误，应为字母+数字，例如 PH01, QH44")
    
    c_type = match.group(1).upper()
    c_seq = int(match.group(2))
    
    if c_type == 'PH':
        duration = 60
        start_minute_of_day = (c_seq - 1) * 60
    elif c_type == 'QH':
        duration = 15
        start_minute_of_day = (c_seq - 1) * 15
    else:
        raise ValueError("仅支持 PH 和 QH 合约")

    if len(end_date) == 10:
        real_end = f"{end_date} 23:59:59"
    else:
        real_end = end_date

    target_hour = start_minute_of_day // 60
    target_minute = start_minute_of_day % 60

    logger.info(f"Analyze Volume Trend: {area} {short_name} (N={hours_before_close}, M={min_points})")

    try:
        # 如果没有高级策略参数，走原来的快速聚合查询 (性能优化)
        if not hours_before_close and not min_points:
            query = db.query(
                cast(Trade.delivery_start, Date).label('date'), 
                func.sum(Trade.volume).label('volume')
            ).filter(
                Trade.delivery_area == area,
                Trade.delivery_start >= start_date,
                Trade.delivery_start <= real_end,
                Trade.duration_minutes >= duration - 0.1,
                Trade.duration_minutes <= duration + 0.1,
                extract('hour', Trade.delivery_start) == target_hour,
                extract('minute', Trade.delivery_start) == target_minute
            ).group_by(
                cast(Trade.delivery_start, Date)
            ).order_by(
                cast(Trade.delivery_start, Date)
            )
            rows = query.all()
            return [{"time": str(r.date), "value": round(r.volume, 2)} for r in rows]

        # === 高级策略模式 ===
        # 我们需要获取更细粒度的数据：[DeliveryDate, TradeTime, Volume]
        # 这里按分钟聚合 TradeTime，减少传输数据量
        query = db.query(
            cast(Trade.delivery_start, Date).label('delivery_date'),
            Trade.delivery_start,
            # 将交易时间截断到分钟 (Postgres语法 date_trunc, SQLite语法不同需兼容，这里用 func.date_trunc)
            # 为了兼容性，我们直接取 trade_time，在 Pandas 里处理 resample
            Trade.trade_time, 
            Trade.volume
        ).filter(
            Trade.delivery_area == area,
            Trade.delivery_start >= start_date,
            Trade.delivery_start <= real_end,
            Trade.duration_minutes >= duration - 0.1,
            Trade.duration_minutes <= duration + 0.1,
            extract('hour', Trade.delivery_start) == target_hour,
            extract('minute', Trade.delivery_start) == target_minute
        ).order_by(
            Trade.delivery_start,
            Trade.trade_time
        )
        
        # 使用 Pandas 处理复杂的时序逻辑
        df = pd.read_sql(query.statement, db.bind)
        
        if df.empty:
            return []

        results = []
        
        # 按“交割日”分组处理 (即每一天作为一个独立的合约样本)
        grouped = df.groupby('delivery_date')
        
        for date, group in grouped:
            # 1. 确定收盘锚点 (以交割开始时间作为收盘基准)
            # Nord Pool 规则：收盘时间通常是交割开始前 1 小时
            close_time = group.iloc[0]['delivery_start'] - timedelta(hours=1)
            
            # 2. 严格的时间过滤
            # 必须同时限制 Start 和 End，防止包含交割后的调整数据
            if hours_before_close:
                start_window = close_time - timedelta(hours=hours_before_close)
                valid_trades = group[
                    (group['trade_time'] >= start_window) & 
                    (group['trade_time'] <= close_time)
                ].copy()
            else:
                # 如果没传 N，默认也只统计到 close_time 为止
                valid_trades = group[group['trade_time'] <= close_time].copy()
            
            if valid_trades.empty:
                results.append({"time": str(date), "value": 0})
                continue

            # 3. 应用聚合点逻辑 (M 个分钟有交易)
            if min_points > 0:
                # 【核心修复】
                # 为了正确计算“第 M 个点”，必须基于连续的时间轴，而不是稀疏的交易记录
                
                # 确定时间轴起点
                if hours_before_close:
                    axis_start = start_window
                else:
                    # 如果没指定 N，就从当天第一笔交易开始算
                    axis_start = valid_trades['trade_time'].min().floor('min')

                # A. 构造连续时间轴 (1分钟级)
                full_idx = pd.date_range(start=axis_start, end=close_time, freq='1min')
                
                # B. 重采样并填充 0
                # 这样即使某分钟没交易，也会有一行 volume=0
                df_resampled = (
                    valid_trades.set_index('trade_time')
                    .resample('1min')
                    .agg({'volume': 'sum'})
                    .reindex(full_idx, fill_value=0)
                )
                
                # C. 寻找触发点 (Activation Time)
                # 只有 volume > 0 的分钟才算聚合点
                df_resampled['is_active'] = (df_resampled['volume'] > 0).astype(int)
                df_resampled['active_cumsum'] = df_resampled['is_active'].cumsum()
                
                # 筛选出累计活跃数达到 M 的所有时刻
                qualified_moments = df_resampled[df_resampled['active_cumsum'] >= min_points]
                
                # 如果整个窗口内活跃分钟数不足 M，则该日有效交易量为 0
                if qualified_moments.empty:
                    # results.append({"time": str(date), "value": 0})
                    continue
                
                # 第 M 个聚合点的时间
                activation_time = qualified_moments.index[0]
                
                # D. 统计有效容量
                # 从激活时间(含)开始，一直到收盘
                final_volume = df_resampled.loc[activation_time:]['volume'].sum()
                
                results.append({"time": str(date), "value": round(final_volume, 2)})
            
            else:
                # 如果没有 M 限制，直接求和
                final_volume = valid_trades['volume'].sum()
                results.append({"time": str(date), "value": round(final_volume, 2)})

        return results

    except Exception as e:
        logger.error(f"Volume Trend Advanced Query Failed: {e}")
        import traceback
        traceback.print_exc()
        raise e

def get_intraday_pattern(db: Session, area: str, short_name: str, start_date: str, end_date: str):
    """
    【新增】分析该合约在交易时段内的微观流动性分布 (分钟级)
    帮助判断：在这个小时内，前10分钟活跃还是最后10分钟活跃？
    """
    match = re.match(r"^([A-Za-z]+)(\d+)$", short_name.strip())
    if not match: raise ValueError("合约简称格式错误")
    c_type = match.group(1).upper()
    c_seq = int(match.group(2))
    
    # 逻辑同上，定位合约
    if c_type == 'PH':
        duration = 60
        start_minute_of_day = (c_seq - 1) * 60
    elif c_type == 'QH':
        duration = 15
        start_minute_of_day = (c_seq - 1) * 15
    else:
        raise ValueError("仅支持 PH 和 QH")

    if len(end_date) == 10: real_end = f"{end_date} 23:59:59"
    else: real_end = end_date
    
    target_hour = start_minute_of_day // 60
    target_minute = start_minute_of_day % 60

    try:
        # 按分钟聚合统计平均成交量
        # trade_time 是实际成交时间
        query = db.query(
            extract('minute', Trade.trade_time).label('minute'),
            func.avg(Trade.volume).label('avg_volume'), # 平均单笔成交量
            func.sum(Trade.volume).label('total_volume'), # 总成交量
            func.count(Trade.trade_id).label('trade_count')
        ).filter(
            Trade.delivery_area == area,
            Trade.delivery_start >= start_date,
            Trade.delivery_start <= real_end,
            Trade.duration_minutes >= duration - 0.1,
            Trade.duration_minutes <= duration + 0.1,
            extract('hour', Trade.delivery_start) == target_hour,
            extract('minute', Trade.delivery_start) == target_minute
        ).group_by(
            extract('minute', Trade.trade_time)
        ).order_by(
            extract('minute', Trade.trade_time)
        )
        
        rows = query.all()
        # 归一化为 "相对开盘时间的分钟数" (0-59)
        result = []
        for r in rows:
            # 这里的 minute 是实际时钟分钟。
            # 如果是 PH01 (00:00-01:00)，minute 就是 0-59。
            # 如果是 QH44 (10:45-11:00)，minute 是 45-59。
            # 我们直接返回实际分钟即可，前端展示
            result.append({
                "minute": int(r.minute),
                "volume": round(r.total_volume, 2)
            })
        return result
    except Exception as e:
        logger.error(f"Intraday Pattern Failed: {e}")
        raise e

def get_price_volume_profile(db: Session, area: str, short_name: str, start_date: str, end_date: str):
    """
    【新增】价格成交分布 (Volume Profile)
    帮助判断：在该段时间内，市场认可的“公允价格”在哪里？
    """
    # ... (合约定位逻辑同上，略) ...
    match = re.match(r"^([A-Za-z]+)(\d+)$", short_name.strip())
    if not match: raise ValueError("合约简称格式错误")
    c_type = match.group(1).upper()
    c_seq = int(match.group(2))
    
    if c_type == 'PH':
        duration = 60
        start_minute_of_day = (c_seq - 1) * 60
    elif c_type == 'QH':
        duration = 15
        start_minute_of_day = (c_seq - 1) * 15
    else:
        raise ValueError("仅支持 PH 和 QH")

    if len(end_date) == 10: real_end = f"{end_date} 23:59:59"
    else: real_end = end_date
    target_hour = start_minute_of_day // 60
    target_minute = start_minute_of_day % 60

    try:
        # 按价格分组
        # 为了防止价格过于稀疏，可以在应用层做 bucket，也可以直接查出来再处理
        # 这里直接查出来，前端 charts 库通常能自适应，或者我们在 Python 里简单分箱
        query = db.query(
            Trade.price,
            func.sum(Trade.volume).label('volume')
        ).filter(
            Trade.delivery_area == area,
            Trade.delivery_start >= start_date,
            Trade.delivery_start <= real_end,
            Trade.duration_minutes >= duration - 0.1,
            Trade.duration_minutes <= duration + 0.1,
            extract('hour', Trade.delivery_start) == target_hour,
            extract('minute', Trade.delivery_start) == target_minute
        ).group_by(
            Trade.price
        ).order_by(
            Trade.price
        )
        
        rows = query.all()
        result = [{"price": r.price, "volume": round(r.volume, 2)} for r in rows]
        return result
    except Exception as e:
        logger.error(f"Volume Profile Failed: {e}")
        raise e