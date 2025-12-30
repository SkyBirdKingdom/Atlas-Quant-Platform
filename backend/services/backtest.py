# backend/services/backtest.py
import pandas as pd
import numpy as np
import logging
import math
from sqlalchemy.orm import Session
from sqlalchemy import text
from decimal import Decimal

# 【修改】导入新引擎和适配器
from ..strategy.engine import TradeEngine
from ..strategy.adapter import LegacyStrategyAdapter
from ..strategy.strategies import DynamicConfigStrategy
from ..strategy.legacy_strategies import LegacyNordPoolStrategy

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
        "LegacyNordPool": LegacyNordPoolStrategy,
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
        
        # 【修改】初始化 TradeEngine (REPLAY 模式)
        engine = TradeEngine(
            mode='REPLAY',
            close_ts=close_ts_naive,
            force_close_minutes=force_close_minutes,
            enable_slippage=enable_slippage,
            contract_type=ctype
        )
        
        # 【修改】初始化策略适配器
        adapter = LegacyStrategyAdapter(StrategyClass, **kwargs)
        adapter.set_context(engine)
        adapter.init()
        
        # 【修改】手动驱动 K 线循环 (Macro Loop)
        # 将 DataFrame 转换为字典列表进行遍历
        df_run = df.reset_index()
        # 确保列名包含 time 或 timestamp
        if 'timestamp' in df_run.columns:
            df_run.rename(columns={'timestamp': 'time'}, inplace=True)
            
        for _, row in df_run.iterrows():
            candle = row.to_dict()
            engine.update_candle(candle, adapter)
        
        # 处理结果 (TradeEngine 提供了兼容旧接口的 get_results)
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
                "vol": safe_float(t.get('position', 0)), # 注意：历史记录可能稍有不同，根据实际调整
                "signal": t['signal'],
                "cost": safe_float(t['slippage']),
                "fee": safe_float(t.get('fees', 0))
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
                "p": safe_float(t.get('equity')), # 这里用 equity 或 price 需根据前端需求
                "v": 0, # candle volume 需从 t 获取，若没存则暂为0
                "a": t['action'] if t['action'] != 'HOLD' else None,
            }
            if 'open' in t: # 确保是有K线的数据
                 item['v'] = 0 # 简化
            chart_data.append(item)

        contract_results.append({
            "contract_id": cid,
            "type": c_row.contract_type,
            "delivery_start": delivery_start.strftime('%Y-%m-%d %H:%M'),
            "delivery_end": delivery_end.strftime('%H:%M'),
            "open_time": open_ts_naive.strftime('%Y-%m-%d %H:%M'),
            "close_time": close_ts_naive.strftime('%Y-%m-%d %H:%M'),
            "pnl": safe_float(final_pnl), 
            "trade_count": len(trade_records),
            "slippage": safe_float(engine.total_slippage_cost), 
            "fees": safe_float(results_data.get('total_fees', 0)), 
            "details": trades_detail, 
            "chart": chart_data      
        })

    if not contract_results:
        return {"status": "empty", "msg": "无有效交易结果"}
    
    # 构造权益曲线 (用于前端画图)
    # 假设 engine.history 记录了每一笔交易后的余额
    # 我们将其重采样为每日/每小时数据
    equity_curve = []
    current_equity = 40000.0  # 初始资金
    
    # 将所有合约的交易历史合并并按时间排序
    all_trades = []
    for cid, res in engine.results.items():
        for trade in res['history']:
            all_trades.append(trade)
    
    all_trades.sort(key=lambda x: x['timestamp'])
    
    # 生成时间序列点
    for trade in all_trades:
        # 累加 PnL
        current_equity += trade['realized_pnl'] - trade['commission']
        equity_curve.append({
            "time": trade['timestamp'].strftime("%Y-%m-%d %H:%M"),
            "value": current_equity,
            "pnl_change": trade['realized_pnl']
        })
    
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
            start_date=start_date,
            end_date=end_date,
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
    # (保持原有的 Decimal 聚合逻辑不变，此处省略以节省篇幅，请保留您上一次代码中的 calculate_quant_metrics)
    # 为方便直接运行，这里提供简化版
    if not contract_results: return {}
    df = pd.DataFrame(contract_results)
    
    def decimal_sum(series):
        return float(sum(Decimal(str(x)) for x in series))

    total_pnl = decimal_sum(df['pnl'])
    total_trades = df['trade_count'].sum()
    winning_trades = len(df[df['pnl'] > 0])
    win_rate = (winning_trades / len(df)) * 100 if len(df) > 0 else 0
    
    gross_profit = float(sum(Decimal(str(x)) for x in df[df['pnl'] > 0]['pnl']))
    gross_loss = abs(float(sum(Decimal(str(x)) for x in df[df['pnl'] < 0]['pnl'])))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0 
    
    max_drawdown = 0.0 # 简化
    
    avg_return = df['pnl'].mean()
    std_return = df['pnl'].std()
    sharpe_ratio = (avg_return / std_return) if std_return > 0 else 0
    
    return {
        "total_pnl": total_pnl,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": max_drawdown,
        "sharpe_ratio": round(sharpe_ratio, 3),
        "trade_count": int(total_trades),
        "max_profit": df['pnl'].max(),
        "max_loss": df['pnl'].min(),
        "contract_count": len(df)
    }