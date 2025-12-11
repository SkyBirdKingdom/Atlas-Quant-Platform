# backend/services/backtest.py
import pandas as pd
import numpy as np
import logging
import math
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..strategy.engine import BacktestEngine
from ..strategy.strategies import DynamicConfigStrategy
from . import feature_engine
from ..utils.time_helper import get_trading_window
from datetime import timedelta

logger = logging.getLogger("Backtest")

def safe_float(value, default=0.0):
    try:
        if value is None: return default
        f_val = float(value)
        if math.isnan(f_val) or math.isinf(f_val): return default
        return f_val
    except: return default

def run_strategy_backtest(db: Session, start_date: str, end_date: str, area: str, 
                          strategy_name: str = "DynamicConfig", **kwargs):
    
    if 'T' not in start_date: start_date += " 00:00:00"
    if 'T' not in end_date: end_date += " 23:59:59"
    
    # 1. 提取参数
    force_close_minutes = kwargs.pop('force_close_minutes', 0)
    enable_slippage = kwargs.pop('enable_slippage', False)
    
    query = text("""
        SELECT DISTINCT contract_id, contract_type, delivery_start
        FROM trades
        WHERE delivery_area = :area 
          AND delivery_start >= :start 
          AND delivery_start <= :end
        ORDER BY delivery_start
    """)
    contracts = db.execute(query, {"area": area, "start": start_date, "end": end_date}).fetchall()
    
    if not contracts:
        return {"status": "empty", "msg": "该时间段无合约数据"}

    StrategyClass = {
        "DynamicConfig": DynamicConfigStrategy, # 新策略
    }.get(strategy_name, DynamicConfigStrategy)

    contract_results = [] # 存放每个合约的汇总结果
    
    # 2. 遍历合约
    for index, c_row in enumerate(contracts):
        cid = c_row.contract_id

        delivery_start = c_row.delivery_start
        duration_min = 60 if c_row.contract_type == 'PH' else 15
        delivery_end = delivery_start + timedelta(minutes=duration_min)
        
        # 计算收盘时间 (Delivery - 1h)
        # 注意：这里需要精准计算，用于传给 Engine 做强平判断
        open_ts, close_ts = get_trading_window(delivery_start)
        # 将带时区的时间转为 naive UTC 以匹配 dataframe (如果 df 是 naive 的)
        open_ts_naive = open_ts.replace(tzinfo=None)
        close_ts_naive = close_ts.replace(tzinfo=None)

        df = feature_engine.get_contract_features(db, cid, area)
        if df.empty: continue
            
        # 初始化引擎 (传入 close_ts 和 buffer)
        engine = BacktestEngine(df, close_ts_naive, force_close_minutes, enable_slippage)
        engine.run(StrategyClass, **kwargs)
        
        # 确保最后平仓 (如果 buffer 没触发或者数据缺损，这里做最后的兜底)
        if engine.current_position != 0:
            engine.execute_order(0, reason="EOF_FORCE_CLOSE")

        # === 单合约统计 ===
        history_df = pd.DataFrame(engine.history)
        
        if history_df.empty:
            continue

        # 最终权益 (Equity) 就是该合约的净利润 (因为最后持仓为0，Equity = Cash)
        final_pnl = history_df.iloc[-1]['equity']
        total_vol = history_df[history_df['action'].isin(['BUY', 'SELL'])]['position'].diff().abs().sum()
        trade_records = history_df[history_df['action'] != 'HOLD'].copy()
        
        # 构造详细交易记录 (用于前端弹窗表格)
        trades_detail = []
        for _, t in trade_records.iterrows():
            trades_detail.append({
                "time": t['time'].strftime('%Y-%m-%d %H:%M'),
                "action": t['action'],
                "price": safe_float(t['price']),
                "vol": safe_float(t['trade_vol']), # 简化逻辑，这里其实应该算 delta
                "signal": t['signal'],
                "cost": safe_float(t['slippage_cost'])
            })
            
        # 构造曲线数据 (用于前端弹窗画图)
        # 只保留必要字段减小体积
        chart_data = []
        for _, t in history_df.iterrows():
            chart_data.append({
                "t": t['time'].strftime('%H:%M'),
                "p": safe_float(t['price']),
                "v": safe_float(t['volume']),
                "a": t['action'] if t['action'] != 'HOLD' else None, # 用于标记买卖点
                "s": t['signal'] if t['action'] != 'HOLD' else None
            })

        contract_results.append({
            "contract_id": cid,
            "type": c_row.contract_type,
            "delivery_start": delivery_start.strftime('%Y-%m-%d %H:%M'),
            "delivery_end": delivery_end.strftime('%H:%M'), # 结束时间只显示时分即可
            "open_time": open_ts_naive.strftime('%Y-%m-%d %H:%M'),
            "close_time": close_ts_naive.strftime('%Y-%m-%d %H:%M'),
            "pnl": safe_float(final_pnl),
            "trade_count": len(trade_records),
            "slippage": safe_float(engine.total_slippage_cost),
            "details": trades_detail, # 详情表数据
            "chart": chart_data       # 图表数据
        })

    # 3. 全局统计
    if not contract_results:
        return {"status": "empty", "msg": "无有效交易结果"}
    
    # 调用专门的计算函数
    summary = calculate_quant_metrics(contract_results)
        
    df_res = pd.DataFrame(contract_results)
    
    df_res.sort_values(by='delivery_start', inplace=True)
    # 按时间/ID排序返回列表
    contract_list = df_res.to_dict(orient='records')

    return {
        "status": "success", 
        "data": {
            "summary": summary,
            "contracts": contract_list
        }
    }

def calculate_quant_metrics(contract_results):
    """
    【核心】计算专业量化指标
    """
    if not contract_results:
        return {}
        
    df = pd.DataFrame(contract_results)
    
    # 1. 基础数据
    total_pnl = df['pnl'].sum()
    total_trades = df['trade_count'].sum()
    winning_trades = len(df[df['pnl'] > 0])
    losing_trades = len(df[df['pnl'] <= 0])
    
    # 2. 胜率 (Win Rate)
    win_rate = (winning_trades / len(df)) * 100 if len(df) > 0 else 0
    
    # 3. 盈亏比 (Profit Factor) = 总盈利 / |总亏损|
    gross_profit = df[df['pnl'] > 0]['pnl'].sum()
    gross_loss = abs(df[df['pnl'] < 0]['pnl'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0 # 避免除零
    
    # 4. 最大回撤 (Max Drawdown)
    # 这里的回撤是基于“合约资金曲线”的。
    # 假设我们按时间顺序交易这些合约，累计盈亏曲线是怎样的？
    # 注意：真实情况是并行交易，这里简化为累加资金曲线来估算
    df_sorted = df.sort_values(by='close_time') # 假设 df 有 close_time
    cumulative_pnl = df_sorted['pnl'].cumsum()
    running_max = cumulative_pnl.cummax()
    drawdown = running_max - cumulative_pnl
    max_drawdown = drawdown.max()
    
    # 5. 夏普比率 (Sharpe Ratio) - 简化版
    # 假设无风险利率为 0
    # Sharpe = 平均收益 / 收益标准差
    avg_return = df['pnl'].mean()
    std_return = df['pnl'].std()
    sharpe_ratio = (avg_return / std_return) if std_return > 0 else 0
    
    return {
        "total_pnl": round(safe_float(total_pnl), 2),
        "win_rate": round(safe_float(win_rate), 1),       # 胜率保留1位
        "profit_factor": round(safe_float(profit_factor), 2),
        "max_drawdown": round(safe_float(max_drawdown), 2),
        "sharpe_ratio": round(safe_float(sharpe_ratio), 3), # 夏普保留3位
        
        "trade_count": int(total_trades),
        "max_profit": round(safe_float(df['pnl'].max()), 2),
        "max_loss": round(safe_float(df['pnl'].min()), 2),
        "contract_count": len(df)
    }