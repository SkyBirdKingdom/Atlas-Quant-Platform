import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from ..models import Trade
from sqlalchemy import func

def analyze_liquidity(db: Session, start_date: str, end_date: str, area: str, target_pos: float):
    # 1. 查询数据：增加 max_price 和 min_price 的基础数据支持
    query = db.query(
        Trade.delivery_start,
        Trade.price,
        Trade.volume,
        Trade.contract_type
    ).filter(
        Trade.delivery_area == area,
        Trade.delivery_start >= f"{start_date} 00:00:00",
        Trade.delivery_start <= f"{end_date} 23:59:59"
    )
    
    rows = query.all()
    if not rows:
        return {"ph": [], "qh": []}

    df = pd.DataFrame([{
        "time": r.delivery_start,
        "price": r.price,
        "volume": r.volume,
        "type": r.contract_type
    } for r in rows])
    
    df['time'] = pd.to_datetime(df['time'])
    
    result = {"ph": [], "qh": []}
    
    for c_type in ['PH', 'QH']:
        sub_df = df[df['type'] == c_type]
        if sub_df.empty:
            continue
            
        # 2. 聚合逻辑升级：增加 min/max 统计
        stats = sub_df.groupby('time').agg(
            total_vol=('volume', 'sum'),
            avg_price=('price', 'mean'),
            std_price=('price', 'std'),
            max_price=('price', 'max'), # 新增：最高成交价
            min_price=('price', 'min')  # 新增：最低成交价
        ).reset_index()
        
        # 填充 NaN
        stats['std_price'] = stats['std_price'].fillna(0)
        stats['max_price'] = stats['max_price'].fillna(stats['avg_price'])
        stats['min_price'] = stats['min_price'].fillna(stats['avg_price'])
        
        # --- 3. 电力市场专用滑点模型 ---
        def calc_power_slippage(row):
            if row['total_vol'] == 0: 
                # 如果完全没量，滑点是无穷大，这里给个惩罚值（比如当前持仓量 * 10 EUR）
                return 50.0 
            
            # A. 市场占比 (Market Share)
            share = target_pos / row['total_vol']
            
            # B. 波动率代理指标 (Volatility Proxy)
            # 逻辑：如果极差(Max-Min)很大，说明盘口很薄，稍微吃一点单子价格就跑了
            # 我们取 标准差 和 半极差 的较大值，作为基础风险度量
            price_range_risk = (row['max_price'] - row['min_price']) * 0.6
            base_volatility = max(row['std_price'], price_range_risk)
            
            # 兜底：如果完全没波动（比如只有1笔成交），给一个基础噪音（比如价格的 1%）
            if base_volatility < 0.1:
                base_volatility = row['avg_price'] * 0.01

            # C. 冲击系数 (Impact Factor)
            # 股票通常用 0.5，电力市场建议 1.5 ~ 2.0
            k_factor = 2.0
            
            # D. 缺乏弹性惩罚 (Inelasticity Penalty)
            # 如果你的占比超过 10%，滑点不再是线性的，而是指数爆炸
            # 使用 Power 指数：从 0.5 (平方根) 提升到 0.8 或 1.0 (线性)
            share_impact = np.power(share, 0.8)
            
            # 最终公式
            slippage = base_volatility * k_factor * share_impact
            
            # 极端情况封顶 (防止 UI 显示太夸张)，比如限制在价格的 50% 以内
            return round(min(slippage, row['avg_price'] * 0.5), 2)

        stats['est_slippage'] = stats.apply(calc_power_slippage, axis=1)
        stats['time_str'] = stats['time'].dt.strftime('%Y-%m-%d %H:%M')
        
        # QH 补全逻辑 (保持不变)
        if c_type == 'QH':
            full_idx = pd.date_range(f"{start_date} 00:00", f"{end_date} 23:45", freq='15min')
            full_df = pd.DataFrame({'time': full_idx})
            full_df['time_str'] = full_df['time'].dt.strftime('%Y-%m-%d %H:%M')
            
            # 只合并数据列，不合并 time_str
            cols_to_merge = stats[['time', 'total_vol', 'avg_price', 'std_price', 'est_slippage', 'max_price', 'min_price']]
            
            merged = pd.merge(full_df, cols_to_merge, on='time', how='left').fillna({
                'total_vol': 0, 'avg_price': 0, 'std_price': 0, 
                'est_slippage': 50.0, # 没数据的时间段，滑点设为高危值
                'max_price': 0, 'min_price': 0
            })
            data_list = merged[['time_str', 'total_vol', 'avg_price', 'std_price', 'est_slippage']].to_dict(orient='records')
        else:
            data_list = stats[['time_str', 'total_vol', 'avg_price', 'std_price', 'est_slippage']].to_dict(orient='records')

        result[c_type.lower()] = data_list

    return result