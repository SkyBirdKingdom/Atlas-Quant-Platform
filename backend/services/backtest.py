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
from decimal import Decimal

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
    
    original_start = start_date
    original_end = end_date
    
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
        ctype = c_row.contract_type 

        delivery_start = c_row.delivery_start
        duration_min = 60 if ctype == 'PH' else 15
        delivery_end = delivery_start + timedelta(minutes=duration_min)
        
        open_ts, close_ts = get_trading_window(delivery_start)
        open_ts_naive = open_ts.replace(tzinfo=None)
        close_ts_naive = close_ts.replace(tzinfo=None)

        df = feature_engine.get_contract_features(db, cid, area)
        if df.empty: continue
            
        engine = BacktestEngine(
            df, 
            close_ts_naive, 
            force_close_minutes, 
            enable_slippage,
            contract_type=ctype
        )
        engine.run(StrategyClass, **kwargs)
        
        if engine.current_position != 0:
            engine.execute_order(0, reason="EOF_FORCE_CLOSE")

        results_data = engine.get_results()
        history_df = pd.DataFrame(results_data['history'])

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
                "cost": safe_float(t['slippage_cost']),
                "fee": safe_float(t.get('fee_cost', 0))
            })
            
        chart_data = []
        for _, t in history_df.iterrows():
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

        # 【核心修改】移除 round，保留原始精度
        contract_results.append({
            "contract_id": cid,
            "type": c_row.contract_type,
            "delivery_start": delivery_start.strftime('%Y-%m-%d %H:%M'),
            "delivery_end": delivery_end.strftime('%H:%M'),
            "open_time": open_ts_naive.strftime('%Y-%m-%d %H:%M'),
            "close_time": close_ts_naive.strftime('%Y-%m-%d %H:%M'),
            "pnl": safe_float(final_pnl), # No round
            "trade_count": len(trade_records),
            "slippage": safe_float(engine.total_slippage_cost), # No round
            "fees": safe_float(results_data.get('total_fees', 0)), # No round
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
                "fees": c.get('fees', 0),
                "txs": c['details']
            })
        
        snapshot_params = kwargs.copy()
        snapshot_params['force_close_minutes'] = force_close_minutes
        snapshot_params['enable_slippage'] = enable_slippage
        
        record = BacktestRecord(
            id=str(uuid.uuid4()),
            strategy_name=strategy_name,
            area=area,
            start_date=original_start,
            end_date=original_end,
            params=snapshot_params,
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
        
    except Exception as e:
        logger.error(f"Save snapshot failed: {e}")

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
    
    # 【核心修改】使用 Decimal 进行聚合，防止 sum() 引入浮点误差
    def decimal_sum(series):
        return float(sum(Decimal(str(x)) for x in series))

    total_pnl = decimal_sum(df['pnl'])
    
    total_trades = df['trade_count'].sum()
    winning_trades = len(df[df['pnl'] > 0])
    win_rate = (winning_trades / len(df)) * 100 if len(df) > 0 else 0
    
    # PnL 分组聚合也要用 Decimal
    gross_profit = float(sum(Decimal(str(x)) for x in df[df['pnl'] > 0]['pnl']))
    gross_loss = abs(float(sum(Decimal(str(x)) for x in df[df['pnl'] < 0]['pnl'])))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0 
    
    # 回撤计算 (保留 float，因为 cumsum/max 相对不敏感且 Decimal 性能较差)
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
    
    # 返回完整精度
    return {
        "total_pnl": total_pnl,
        "win_rate": round(win_rate, 2), # Win rate 百分比可以保留2位
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": max_drawdown,
        "sharpe_ratio": round(sharpe_ratio, 3),
        "trade_count": int(total_trades),
        "max_profit": df['pnl'].max(),
        "max_loss": df['pnl'].min(),
        "contract_count": len(df)
    }