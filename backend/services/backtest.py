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
import uuid
from ..models import BacktestRecord

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
        "DynamicConfig": DynamicConfigStrategy, 
    }.get(strategy_name, DynamicConfigStrategy)

    contract_results = [] 
    
    for index, c_row in enumerate(contracts):
        cid = c_row.contract_id

        delivery_start = c_row.delivery_start
        duration_min = 60 if c_row.contract_type == 'PH' else 15
        delivery_end = delivery_start + timedelta(minutes=duration_min)
        
        open_ts, close_ts = get_trading_window(delivery_start)
        open_ts_naive = open_ts.replace(tzinfo=None)
        close_ts_naive = close_ts.replace(tzinfo=None)

        df = feature_engine.get_contract_features(db, cid, area)
        if df.empty: continue
            
        engine = BacktestEngine(df, close_ts_naive, force_close_minutes, enable_slippage)
        engine.run(StrategyClass, **kwargs)
        
        if engine.current_position != 0:
            engine.execute_order(0, reason="EOF_FORCE_CLOSE")

        history_df = pd.DataFrame(engine.history)
        if history_df.empty: continue

        final_pnl = history_df.iloc[-1]['equity']
        trade_records = history_df[history_df['action'] != 'HOLD'].copy()
        
        trades_detail = []
        for _, t in trade_records.iterrows():
            trades_detail.append({
                "time": t['time'].strftime('%Y-%m-%d %H:%M'),
                "action": t['action'],
                "price": safe_float(t['price']),
                "vol": safe_float(t['trade_vol']), 
                "signal": t['signal'],
                "cost": safe_float(t['slippage_cost'])
            })
            
        chart_data = []
        for _, t in history_df.iterrows():
            # === 核心修改：无条件透传所有分钟数据 ===
            # 即使 volume=0，也要传 OHLC，保持图表连续性
            ts_seconds = int(t['time'].timestamp())
            item = {
                "t": ts_seconds,
                "o": safe_float(t.get('open')),
                "h": safe_float(t.get('high')),
                "l": safe_float(t.get('low')),
                "c": safe_float(t.get('close')),
                "p": safe_float(t['price']),
                "v": safe_float(t['volume']),
                "a": t['action'] if t['action'] != 'HOLD' else None,
                "s": t['signal'] if t['action'] != 'HOLD' else None
            }
            chart_data.append(item)

        contract_results.append({
            "contract_id": cid,
            "type": c_row.contract_type,
            "delivery_start": delivery_start.strftime('%Y-%m-%d %H:%M'),
            "delivery_end": delivery_end.strftime('%H:%M'),
            "open_time": open_ts_naive.strftime('%Y-%m-%d %H:%M'),
            "close_time": close_ts_naive.strftime('%Y-%m-%d %H:%M'),
            "pnl": round(safe_float(final_pnl), 2),
            "trade_count": len(trade_records),
            "slippage": round(safe_float(engine.total_slippage_cost), 2),
            "details": trades_detail, 
            "chart": chart_data      
        })

    if not contract_results:
        return {"status": "empty", "msg": "无有效交易结果"}
    
    summary = calculate_quant_metrics(contract_results)

    try:
        slim_contracts = []
        for c in contract_results:
            slim_contracts.append({
                "cid": c['contract_id'],
                "type": c['type'],
                "start": c['delivery_start'],
                "end": c['delivery_end'],
                "open_t": c['open_time'],
                "close_t": c['close_time'],
                "pnl": c['pnl'],
                "cnt": c['trade_count'],
                "slip": c['slippage'],
                "txs": c['details']
            })
        # 提取参数 (去掉系统参数，只留策略参数)
        # 注意：kwargs 已经被 pop 过了 force_close_minutes 等，剩下的就是 rules 等
        # 我们最好把 force_close_minutes 等关键风控参数也存进去
        snapshot_params = kwargs.copy()
        snapshot_params['force_close_minutes'] = force_close_minutes
        snapshot_params['enable_slippage'] = enable_slippage
        # 刚才 pop 出去的参数，如果想存，得手动加回来，或者在 pop 之前备份
        # 这里简单处理，假设 kwargs 里剩下的就是核心策略参数 (rules)
        
        record = BacktestRecord(
            id=str(uuid.uuid4()),
            strategy_name=strategy_name,
            area=area,
            start_date=start_date,
            end_date=end_date,
            params=snapshot_params, # 存入 JSON
            
            # 核心指标
            total_pnl=summary['total_pnl'],
            sharpe_ratio=summary['sharpe_ratio'],
            max_drawdown=summary['max_drawdown'],
            profit_factor=summary['profit_factor'],
            win_rate=summary['win_rate'],
            trade_count=summary['trade_count'],
            contract_stats=slim_contracts
        )
        db.add(record)
        db.commit()
        logger.info(f"💾 回测快照已保存: {record.id}")
        
    except Exception as e:
        logger.error(f"❌ 保存回测快照失败: {e}")
        # 不影响主流程返回

    df_res = pd.DataFrame(contract_results)
    df_res.sort_values(by='delivery_start', inplace=True)
    contract_list = df_res.to_dict(orient='records')

    return {
        "status": "success", 
        "data": {
            "summary": summary,
            "contracts": contract_list
        }
    }

def calculate_quant_metrics(contract_results):
    if not contract_results: return {}
    df = pd.DataFrame(contract_results)
    
    total_pnl = df['pnl'].sum()
    total_trades = df['trade_count'].sum()
    winning_trades = len(df[df['pnl'] > 0])
    win_rate = (winning_trades / len(df)) * 100 if len(df) > 0 else 0
    
    gross_profit = df[df['pnl'] > 0]['pnl'].sum()
    gross_loss = abs(df[df['pnl'] < 0]['pnl'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0 
    
    if not df.empty and 'close_time' in df.columns:
        df_sorted = df.sort_values(by='close_time') 
        cumulative_pnl = df_sorted['pnl'].cumsum()
        running_max = cumulative_pnl.cummax()
        drawdown = running_max - cumulative_pnl
        max_drawdown = drawdown.max()
    else:
        max_drawdown = 0.0
    
    avg_return = df['pnl'].mean()
    std_return = df['pnl'].std()
    sharpe_ratio = (avg_return / std_return) if std_return > 0 else 0
    
    return {
        "total_pnl": round(safe_float(total_pnl), 2),
        "win_rate": round(safe_float(win_rate), 1),
        "profit_factor": round(safe_float(profit_factor), 2),
        "max_drawdown": round(safe_float(max_drawdown), 2),
        "sharpe_ratio": round(safe_float(sharpe_ratio), 3),
        "trade_count": int(total_trades),
        "max_profit": round(safe_float(df['pnl'].max()), 2),
        "max_loss": round(safe_float(df['pnl'].min()), 2),
        "contract_count": len(df)
    }