# backend/services/optimizer.py
import itertools
import logging
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text
import uuid
from decimal import Decimal

# 【修改】导入新组件
from ..strategy.engine import TradeEngine
from ..strategy.adapter import LegacyStrategyAdapter
from ..strategy.strategies import DynamicConfigStrategy
from ..services import feature_engine
from ..utils.time_helper import get_trading_window

logger = logging.getLogger("Optimizer")

task_status = {} # task_id -> {status, progress, result}

def run_optimization_async(task_id: str, db: Session, req_data: dict):
    # (保持原有逻辑不变，省略)
    try:
        run_grid_search(db, req_data['base'], req_data['grid'], task_id)
    except Exception as e:
        task_status[task_id] = {"status": "failed", "error": str(e)}

def run_grid_search(db: Session, base_req: dict, param_grid: dict, task_id: str):
    area = base_req.get('area', 'SE3')
    start_date = base_req.get('start_date')
    end_date = base_req.get('end_date')
    
    if 'T' not in start_date: start_date += " 00:00:00"
    if 'T' not in end_date: end_date += " 23:59:59"

    # 1. 查合约
    query = text("""
        SELECT DISTINCT contract_id, contract_type, delivery_start
        FROM trades
        WHERE delivery_area = :area 
          AND delivery_start >= :start 
          AND delivery_start <= :end
        ORDER BY delivery_start
    """)
    contracts = db.execute(query, {"area": area, "start": start_date, "end": end_date}).fetchall()
    
    # 2. 预加载数据
    preload_data = {} 
    for row in contracts:
        cid = row.contract_id
        df = feature_engine.get_contract_features(db, cid, area)
        if not df.empty:
            open_ts, close_ts = get_trading_window(row.delivery_start)
            preload_data[cid] = {
                "df": df,
                "close_ts": close_ts.replace(tzinfo=None),
                "type": row.contract_type
            }

    # 3. 生成参数组合
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combinations = list(itertools.product(*values))
    
    results = []
    total_combs = len(combinations)
    
    for idx, combo in enumerate(combinations):
        current_params = base_req.copy()
        combo_desc = {}
        
        # 覆盖参数
        for k_idx, key in enumerate(keys):
            val = combo[k_idx]
            combo_desc[key] = val
            # 处理嵌套参数 (rules)
            if key.startswith('rules.'):
                # 简化处理：假设只调参阈值，这里需根据实际 param_grid 结构解析
                pass 
            else:
                current_params[key] = val
                
        # 运行回测 (遍历所有合约)
        total_pnl = Decimal("0")
        
        for cid, data in preload_data.items():
            # 【修改】使用 TradeEngine + Adapter
            engine = TradeEngine(
                mode='REPLAY',
                close_ts=data['close_ts'],
                force_close_minutes=current_params.get('force_close_minutes', 0),
                enable_slippage=current_params.get('enable_slippage', False),
                contract_type=data['type']
            )
            
            # 策略配置
            strategy_params = {
                "max_pos": current_params.get('max_pos', 5.0),
                "rules": current_params.get('rules', {}) # 需从 combo 更新规则
            }
            
            adapter = LegacyStrategyAdapter(DynamicConfigStrategy, **strategy_params)
            adapter.set_context(engine)
            adapter.init()
            
            # 手动循环
            df_run = data['df'].reset_index()
            if 'timestamp' in df_run.columns:
                df_run.rename(columns={'timestamp': 'time'}, inplace=True)

            for _, row in df_run.iterrows():
                candle = row.to_dict()
                engine.update_candle(candle, adapter)
            
            # 累加 PnL
            res = engine.get_results()
            if res['history']:
                # 简单累加最后 Equity - 初始 Cash(0)
                # 注意：history最后一个点的 equity 即为该合约最终 PnL
                pnl = res['history'][-1]['equity']
                total_pnl += Decimal(str(pnl))
        
        results.append({
            "params": combo_desc,
            "total_pnl": float(total_pnl)
        })
        
        # 更新进度
        task_status[task_id] = {
            "status": "running",
            "progress": int((idx + 1) / total_combs * 100)
        }

    # 排序取最优
    results.sort(key=lambda x: x['total_pnl'], reverse=True)
    task_status[task_id] = {
        "status": "completed",
        "result": results[:10] # Top 10
    }