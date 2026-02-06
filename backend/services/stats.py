from sqlalchemy.orm import Session
from sqlalchemy import func, text, extract, cast, Date
from datetime import timedelta, datetime, timezone
from ..models import Trade
import pandas as pd
import re
import logging
import random

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

def get_intraday_volume_profile_analysis(
    db: Session, 
    area: str, 
    short_name: str, 
    start_date: str, 
    end_date: str
):
    """
    【内存优化版】生成分钟级成交进度分布分析数据
    改为逐个合约迭代处理，大幅降低内存占用。
    """
    import re
    import pandas as pd
    import numpy as np
    from sqlalchemy import text
    
    # 1. 解析合约短名 (逻辑不变)
    match = re.match(r"^([A-Za-z]+)(\d+)$", short_name.strip())
    if not match: raise ValueError("合约格式错误")
    c_type, c_seq = match.group(1).upper(), int(match.group(2))
    
    if c_type == 'PH': start_minute_of_day = (c_seq - 1) * 60
    elif c_type == 'QH': start_minute_of_day = (c_seq - 1) * 15
    else: raise ValueError("不支持的类型")
    target_hour, target_minute = start_minute_of_day // 60, start_minute_of_day % 60

    # 2. 获取合约列表 (逻辑不变)
    contracts_query = text("""
        SELECT contract_id, delivery_start 
        FROM trades 
        WHERE delivery_area = :area AND contract_type = :ctype
          AND (delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm')::date >= :start_date
          AND (delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm')::date <= :end_date
          AND EXTRACT(HOUR FROM delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm') = :target_hour
          AND EXTRACT(MINUTE FROM delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm') = :target_minute
        GROUP BY contract_id, delivery_start
        ORDER BY delivery_start
    """)
    contracts = db.execute(contracts_query, {
        "area": area, "ctype": c_type, 
        "start_date": start_date, "end_date": end_date,
        "target_hour": target_hour, "target_minute": target_minute
    }).fetchall()

    if not contracts:
        return {"short_name": short_name, "sample_days": 0, "timeline": []}

    # === 优化核心：不再一次性拉取所有 trades ===
    
    # 初始化时间轴桶 (Timeline Buckets)
    # 这是一个字典，key 是 offset (-240, -235...), value 是一个 list，存放各个合约在该时刻的 pct
    timeline_points = list(range(-240, 5, 5))
    buckets = {t: [] for t in timeline_points}
    
    valid_contract_count = 0

    # 3. 逐个合约循环处理
    for c in contracts:
        cid = c.contract_id
        d_start = pd.to_datetime(c.delivery_start)
        close_time = d_start - pd.Timedelta(hours=1) # 收盘时间

        # 3.1 仅查询当前合约的 trades
        # 优化：只查 sum(volume) 和 time，甚至可以直接在 SQL 算好 cumsum，但为了简单这里先查明细
        # 由于单合约数据量小 (几百几千行)，这里 fetchall 不会炸内存
        t_query = text("""
            SELECT trade_time, volume 
            FROM trades 
            WHERE contract_id = :cid 
            ORDER BY trade_time ASC
        """)
        trades = db.execute(t_query, {"cid": cid}).fetchall()
        
        if not trades: continue
        
        # 3.2 Pandas 处理单合约
        df = pd.DataFrame(trades, columns=['trade_time', 'volume'])
        
        total_vol = df['volume'].sum()
        if total_vol <= 0: continue
        
        valid_contract_count += 1
        
        # 计算 offset minutes
        df['offset'] = (pd.to_datetime(df['trade_time']) - close_time).dt.total_seconds() / 60
        
        # 计算累积百分比曲线
        df['cum_pct'] = df['volume'].cumsum() / total_vol
        
        # 3.3 采样 (Resample / Merge AsOf)
        # 我们需要知道在 -240, -235 ... 时刻的 cum_pct 是多少
        # 构造目标点
        df_target = pd.DataFrame({'target_offset': timeline_points})
        
        # 使用 merge_asof 找到最近的过去时刻的值
        df = df.sort_values('offset')
        merged = pd.merge_asof(
            df_target, 
            df[['offset', 'cum_pct']], 
            left_on='target_offset', 
            right_on='offset', 
            direction='backward'
        )
        
        # 填充 NaN：如果在最开始之前没有数据，说明进度为 0
        merged['cum_pct'] = merged['cum_pct'].fillna(0)
        
        # 3.4 将结果放入桶中
        for _, row in merged.iterrows():
            t_off = int(row['target_offset'])
            val = row['cum_pct']
            buckets[t_off].append(val)
            
        # 手动删除 DataFrame 释放内存 (虽有 GC，但显式删除更保险)
        del df, merged, trades
    
    # 4. 聚合统计 (计算中位数)
    median_curve = []
    for t in timeline_points:
        vals = buckets[t]
        if vals:
            # 计算中位数
            med_val = float(np.median(vals))
            # 也可以保留原始数据用于画箱线图，这里仅保留 list
            # 为了前端不过载，如果样本太多，可以只随机保留一部分散点
            display_points = [round(x, 4) for x in vals]
        else:
            med_val = 0
            display_points = []
            
        median_curve.append({
            "time_offset": t, 
            "label": f"{t}m", 
            "value": round(med_val, 4),
            "raw_data": display_points 
        })
        
    return {
        "short_name": short_name,
        "sample_days": valid_contract_count,
        "timeline": median_curve,
        "date_range": f"{start_date} ~ {end_date}"
    }

def get_first_real_order_time(db: Session, contract_id: str, close_time: datetime, analysis_start: datetime):
    """
    【预留接口】获取该合约历史上第一笔真实提交订单的时间。
    目前数据库无数据，使用 Mock 模拟返回一个介于 [收盘前3小时, 收盘前30分钟] 之间的时间点。
    """
    # TODO: 未来替换为真实 SQL 查询
    # query = text("SELECT created_at FROM orders WHERE contract_id = :cid AND status='submitted' ORDER BY created_at ASC LIMIT 1")
    # result = db.execute(query, {"cid": contract_id}).scalar()
    # return result
    
    # === Mock 模拟逻辑 ===
    # 随机生成一个标记时间，位于分析开始后 1小时 到 收盘前 30分钟 之间
    # analysis_start (Close-4h) ... [Marker] ... Close
    
    total_seconds = (close_time - analysis_start).total_seconds()
    # 假设订单通常在收盘前 1-2 小时产生
    # 也就是距离 analysis_start 过了 2-3 小时 (7200s - 10800s)
    offset = random.randint(3600, 12600) # 1小时到3.5小时
    return analysis_start + timedelta(seconds=offset)

def analyze_liquidation_model(
    db: Session, 
    area: str, 
    short_name: str, 
    start_date: str, 
    end_date: str
):
    """
    清算时间模型回测分析
    逻辑：
    1. 窗口 A: [收盘前4小时 -> 标记时间] -> 算流速 (Flow Rate)
    2. 窗口 B: [标记时间 -> 收盘] -> 算推断量 (Projected) vs 真实量 (Actual)
    """
    # 1. 解析合约
    match = re.match(r"^([A-Za-z]+)(\d+)$", short_name.strip())
    if not match: raise ValueError("合约格式错误")
    c_type = match.group(1).upper()
    c_seq = int(match.group(2))
    
    if c_type == 'PH':
        start_minute = (c_seq - 1) * 60
    elif c_type == 'QH':
        start_minute = (c_seq - 1) * 15
    else: raise ValueError("不支持的合约类型")

    target_hour = start_minute // 60
    target_minute = start_minute % 60

    # 2. 查找合约 (复用之前的时区转换逻辑)
    contracts_query = text("""
        SELECT contract_id, delivery_start 
        FROM trades 
        WHERE delivery_area = :area 
          AND contract_type = :ctype
          AND (delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm')::date >= :start_date
          AND (delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm')::date <= :end_date
          AND EXTRACT(HOUR FROM delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm') = :target_hour
          AND EXTRACT(MINUTE FROM delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm') = :target_minute
        GROUP BY contract_id, delivery_start
        ORDER BY delivery_start
    """)
    
    contracts = db.execute(contracts_query, {
        "area": area, "ctype": c_type, 
        "start_date": start_date, "end_date": end_date,
        "target_hour": target_hour, "target_minute": target_minute
    }).fetchall()

    results = []
    
    for c in contracts:
        cid = c.contract_id
        d_start = c.delivery_start # UTC
        
        # 确定关键时间点
        close_time = d_start - timedelta(hours=1)
        analysis_start = close_time - timedelta(hours=4)
        
        # 3. 获取标记时间 (Marker Time)
        marker_time = get_first_real_order_time(db, cid, close_time, analysis_start)
        
        if not marker_time or marker_time >= close_time or marker_time <= analysis_start:
            continue # 异常数据跳过

        # 4. 查询数据
        # 一次性查出该合约在窗口内的所有交易
        # 稍微放宽一点范围防止边界丢失
        q_trades = text("""
            SELECT trade_time, volume 
            FROM trades 
            WHERE contract_id = :cid 
              AND trade_time >= :start 
              AND trade_time <= :end
        """)
        trades = db.execute(q_trades, {"cid": cid, "start": analysis_start, "end": close_time}).fetchall()
        
        if not trades: continue
        
        df = pd.DataFrame(trades, columns=['trade_time', 'volume'])
        df['trade_time'] = pd.to_datetime(df['trade_time'])
        
        # 5. 切分数据计算
        # 窗口 A (Reference Window)
        mask_ref = (df['trade_time'] >= analysis_start) & (df['trade_time'] < marker_time)
        vol_ref = df.loc[mask_ref, 'volume'].sum()
        
        # 计算流速 (MW / min)
        minutes_ref = (marker_time - analysis_start).total_seconds() / 60
        if minutes_ref <= 0: continue
        flow_rate = vol_ref / minutes_ref
        
        # 窗口 B (Projection Window)
        mask_act = (df['trade_time'] >= marker_time) & (df['trade_time'] <= close_time)
        vol_actual = df.loc[mask_act, 'volume'].sum()
        
        # 计算推断量
        minutes_remaining = (close_time - marker_time).total_seconds() / 60
        vol_projected = flow_rate * minutes_remaining
        
        # 计算百分比 (Projected / Actual)
        # 如果 Actual 为 0 (收盘前完全没交易)，给一个特殊标记或 0
        if vol_actual == 0:
            ratio_pct = 0.0 # 或者 None
        else:
            ratio_pct = (vol_projected / vol_actual) * 100

        # 转换为本地时间用于显示 (Date string)
        # delivery_start is UTC -> Stockholm Date
        # 这里简单处理，直接取 Date 部分，前端根据 delivery_start 知道是哪天
        
        results.append({
            "contract_id": cid,
            "delivery_date": d_start.strftime("%Y-%m-%d"), # 这里的日期可能和 CET 日期差一天，前端自行理解
            "marker_time": marker_time, # datetime
            "avg_flow_rate": round(flow_rate, 2),
            "projected_vol": round(vol_projected, 2),
            "actual_vol": round(vol_actual, 2),
            "percentage": round(ratio_pct, 2)
        })

    return results

def verify_ttl_model(
    db: Session, 
    area: str, 
    short_name: str, 
    start_date: str, 
    end_date: str,
    lookback_minutes: int = 15,  # 回看窗口 (测流速)
    horizon_cap: int = 60        # 预测上限 (置信时间)
):
    """
    验证 TTL (Time-To-Liquidation) 模型在历史数据上的表现
    比较: [基于过去N分钟流速推算的容量] vs [未来有效时间内真实的容量]
    """
    import pandas as pd
    import numpy as np
    from sqlalchemy import text
    from datetime import timedelta
    
    # 1. 解析合约 (复用之前的逻辑)
    match = re.match(r"^([A-Za-z]+)(\d+)$", short_name.strip())
    if not match: raise ValueError("合约格式错误")
    c_type = match.group(1).upper()
    c_seq = int(match.group(2))
    
    if c_type == 'PH': start_minute = (c_seq - 1) * 60
    elif c_type == 'QH': start_minute = (c_seq - 1) * 15
    else: raise ValueError("不支持的合约类型")

    target_hour = start_minute // 60
    target_minute = start_minute % 60

    # 2. 查找合约
    contracts_query = text("""
        SELECT contract_id, delivery_start 
        FROM trades 
        WHERE delivery_area = :area 
          AND contract_type = :ctype
          AND (delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm')::date >= :start_date
          AND (delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm')::date <= :end_date
          AND EXTRACT(HOUR FROM delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm') = :target_hour
          AND EXTRACT(MINUTE FROM delivery_start AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm') = :target_minute
        ORDER BY delivery_start
    """)
    
    contracts = db.execute(contracts_query, {
        "area": area, "ctype": c_type, 
        "start_date": start_date, "end_date": end_date,
        "target_hour": target_hour, "target_minute": target_minute
    }).fetchall()

    if not contracts:
        return {"error": "无合约数据"}

    daily_stats = []
    all_points = [] # 用于散点图：x=time_to_close, y=safety_ratio

    for c in contracts:
        cid = c.contract_id
        d_start = c.delivery_start # UTC
        close_time = d_start - timedelta(hours=1)
        
        # 3. 拉取全量 Trade 数据
        # 范围：收盘前 4小时 到 收盘
        analysis_start = close_time - timedelta(hours=4)
        
        q_trades_agg = text("""
            SELECT 
                date_trunc('minute', trade_time) as minute_ts, 
                SUM(volume) as vol
            FROM trades 
            WHERE contract_id = :cid 
              AND trade_time >= :start 
              AND trade_time <= :end
            GROUP BY 1
            ORDER BY 1 ASC
        """)
        
        # 此时返回的数据量只有几百行，内存占用几乎为零
        agg_rows = db.execute(q_trades_agg, {
            "cid": cid, 
            "start": analysis_start, 
            "end": close_time
        }).fetchall()
        
        if not agg_rows: 
            continue
            
        # 直接构建聚合后的 DataFrame
        # 注意：SQL返回的 minute_ts 可能是 datetime 对象或字符串，pandas 能自动处理
        df_res = pd.DataFrame(agg_rows, columns=['minute_ts', 'volume'])
        df_res['minute_ts'] = pd.to_datetime(df_res['minute_ts'])
        df_res.set_index('minute_ts', inplace=True)
        
        # 重采样对齐时间轴 (Reindex) 以填补没有交易的分钟（补0）
        full_idx = pd.date_range(start=analysis_start, end=close_time, freq='1min')
        df_res = df_res.reindex(full_idx, fill_value=0)
        
        # === 核心计算逻辑 ===
        
        # A. 计算过去流速 (保持不变)
        df_res['past_vol_sum'] = df_res['volume'].rolling(window=lookback_minutes, min_periods=1).sum()
        df_res['flow_rate'] = df_res['past_vol_sum'] / lookback_minutes
        
        # B. 计算有效时间 (保持不变)
        close_ts = pd.Timestamp(close_time)
        df_res['mins_to_close'] = (close_ts - df_res.index).total_seconds() / 60.0
        df_res['horizon'] = df_res['mins_to_close'].clip(upper=horizon_cap)
        
        # C. 计算模型预测容量 (保持不变)
        df_res['predicted_cap'] = df_res['flow_rate'] * df_res['horizon']
        
        # D. 计算真实未来容量 (使用 CumSum 算法优化，性能提升 100x)
        # 1. 预计算累积成交量
        # values 转换为 numpy array 加速访问
        cumsum_vals = df_res['volume'].cumsum().values 
        horizon_mins = df_res['horizon'].astype(int).values
        n_rows = len(df_res)
        
        realized_vols = []
        
        # 使用索引访问比 DataFrame.loc 快得多
        for i in range(n_rows):
            h = horizon_mins[i]
            if h <= 0:
                realized_vols.append(0)
                continue
            
            # 计算目标结束索引
            # 注意：我们要算的是未来 h 分钟，即 (current_time, current_time + h]
            # 对应的数组索引是 i (当前) 到 i + h
            target_idx = min(i + h, n_rows - 1)
            
            # 利用 CumSum 性质：Sum(i+1 ... target) = CumSum[target] - CumSum[i]
            # 假设 df_res['volume'] 在索引 i 处的值包含在 cumsum_vals[i] 中
            # 我们需要的是未来产生的量，不包含当前这一分钟(假设当前已经流逝或作为基准)
            # 这里的逻辑取决于你的定义。通常预测未来是从 "Next Minute" 开始。
            # 即使包含当前分钟，CumSum 差值法也是 O(1)
            
            vol = cumsum_vals[target_idx] - cumsum_vals[i]
            realized_vols.append(vol)
            
        df_res['realized_cap'] = realized_vols
        
        # E. 计算偏差 (Ratio) & 风险标记
        # Ratio = Predicted / Realized
        # Ratio > 1.0 (100%) 意味着危险 (预测 > 真实)
        # 为了避免除以0，做处理
        df_res['ratio'] = np.where(
            df_res['realized_cap'] > 0.01, 
            df_res['predicted_cap'] / df_res['realized_cap'], 
            # 如果真实是0，且预测>0，则是无穷大风险(999)；如果预测也是0，则安全(0)
            np.where(df_res['predicted_cap'] > 0, 999.0, 0.0)
        )
        
        # 5. 统计该日的表现
        # 我们只关心 flow_rate > 0.1 的活跃时段，静默期预测偏差点没关系
        active_df = df_res[df_res['flow_rate'] > 0.1].copy()
        
        if active_df.empty:
            continue
            
        # 统计过激(Overestimated)的分钟数
        danger_moments = active_df[active_df['ratio'] > 1.0]
        danger_pct = len(danger_moments) / len(active_df) * 100
        
        # 收集每一分钟的数据用于绘图 (降采样一下，每5分钟取一个点，避免前端爆炸)
        plot_df = active_df.iloc[::5] 
        for ts, row in plot_df.iterrows():
            all_points.append({
                "mins_to_close": round(row['mins_to_close'], 1),
                "ratio": round(row['ratio'] * 100, 1), # %
                "flow_rate": round(row['flow_rate'], 1)
            })

        daily_stats.append({
            "date": d_start.strftime("%Y-%m-%d"),
            "avg_flow": round(active_df['flow_rate'].mean(), 2),
            "danger_pct": round(danger_pct, 1), # 有多少时间处于危险估算状态
            "max_ratio": round(active_df['ratio'].max() * 100, 1)
        })

    return {
        "daily_stats": daily_stats,
        "scatter_points": all_points
    }