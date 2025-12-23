# backend/services/optimizer.py
import itertools
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import text
from . import feature_engine
from ..strategy.engine import BacktestEngine
from ..strategy.strategies import DynamicConfigStrategy
from ..utils.time_helper import get_trading_window
from datetime import timedelta
import logging

logger = logging.getLogger("Optimizer")

def run_grid_search(db: Session, base_req: dict, param_grid: dict):
    """
    【网格搜索核心逻辑】
    """
    area = base_req.get('area', 'SE3')
    start_date = base_req.get('start_date')
    end_date = base_req.get('end_date')
    
    # 1. 预加载数据 (Preload)
    # 为了速度，我们把涉及到的所有合约数据一次性拉到内存里
    preload_data = {} 
    
    # 注意：这里需要稍微扩宽一点查询范围以防时区问题，或者严格按日期查
    query = text("""
        SELECT DISTINCT contract_id, contract_type, delivery_start
        FROM trades
        WHERE delivery_area = :area 
          AND delivery_start >= :start 
          AND delivery_start <= :end
        ORDER BY delivery_start
    """)
    contracts = db.execute(query, {
        "area": area, 
        "start": start_date + " 00:00:00", 
        "end": end_date + " 23:59:59"
    }).fetchall()
    
    if not contracts:
        return {"status": "empty", "msg": "该时间段无合约数据"}

    logger.info(f"🔥 [Optimizer] 正在预加载 {len(contracts)} 个合约的数据...")
    
    for row in contracts:
        cid = row.contract_id
        # 获取带指标的 DataFrame
        df = feature_engine.get_contract_features(db, cid, area)
        if not df.empty:
            # 去除预热数据 (同 backtest.py 逻辑)
            # 这里简单处理，假设 feature_engine 返回的已经是清洗好的
            # 实际上可能需要像 backtest.py 那样做一次切片，为了性能这里暂略
            
            open_ts, close_ts = get_trading_window(row.delivery_start)
            preload_data[cid] = {
                "df": df,
                "close_ts": close_ts.replace(tzinfo=None)
            }

    logger.info(f"🔥 [Optimizer] 数据预加载完成，开始生成参数组合...")

    # 2. 生成参数组合 (Cartesian Product)
    # param_grid: {"rsi_buy": [20, 30], "rsi_sell": [70, 80]}
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combinations = list(itertools.product(*values))
    
    logger.info(f"🔥 [Optimizer] 即将执行 {len(combinations)} 次全量回测...")
    
    results = []
    
    # 3. 循环回测 (Loop)
    for i, combo in enumerate(combinations):
        current_params = dict(zip(keys, combo))
        
        # 3.1 动态修改策略规则
        # 将扁平参数 (rsi_buy=30) 注入到复杂的 rules 结构中
        run_rules = _apply_params_to_rules(base_req.get('rules', {}), current_params)
        
        total_pnl = 0.0
        total_trades = 0
        winning_trades = 0
        
        # 3.2 遍历所有合约跑回测
        for cid, data in preload_data.items():
            # 初始化引擎
            engine = BacktestEngine(
                data['df'], 
                data['close_ts'], 
                force_close_minutes=base_req.get('force_close_minutes', 0),
                enable_slippage=base_req.get('enable_slippage', False)
            )
            
            # 初始化策略实例
            strategy = DynamicConfigStrategy()
            strategy.rules = run_rules
            strategy.max_pos = base_req.get('max_pos', 5.0)

            # 【新增】赋值止盈止损
            strategy.take_profit_pct = base_req.get('take_profit_pct', 0.0)
            strategy.stop_loss_pct = base_req.get('stop_loss_pct', 0.0)
            
            # 运行 (使用新方法 run_custom_strategy)
            engine.run_custom_strategy(strategy)
            
            if engine.current_position != 0:
                engine.execute_order(0, reason="EOF")
                
            # 统计结果
            hist = engine.history
            if hist:
                final_equity = hist[-1]['equity']
                total_pnl += final_equity
                
                # 简单统计胜率 (为了性能不转 DataFrame)
                # 只要有 trade_vol > 0 就算一次交易? 
                # 这里为了快，只算 PnL，胜率稍微估算一下
                if final_equity > 0: winning_trades += 1
                if final_equity != 0: total_trades += 1

        # 3.3 记录该组参数的最终成绩
        results.append({
            "params": current_params, # {rsi_buy: 30, ...}
            "pnl": round(total_pnl, 2),
            "trades": total_trades,
            "win_rate": round(winning_trades / len(preload_data) * 100, 1) if preload_data else 0
        })
        
        if (i+1) % 10 == 0:
            logger.info(f"   ...进度: {i+1}/{len(combinations)}")
            
    # 4. 排序：按 PnL 从高到低
    results.sort(key=lambda x: x['pnl'], reverse=True)
    
    return {
        "status": "success",
        "results": results,
        "param_names": keys # ["rsi_buy", "rsi_sell"] 方便前端画轴
    }

def _apply_params_to_rules(base_rules, params):
    """
    将优化参数注入到规则模板中
    约定：参数名必须与规则中的 'indicator' 某种映射，或者我们硬编码处理常见参数
    """
    import copy
    new_rules = copy.deepcopy(base_rules)
    
    # 硬编码适配逻辑 (适配 DynamicConfigStrategy)
    # 如果参数名是 'rsi_buy'，我们就去 buy 规则里找 'RSI'
    
    if 'rsi_buy' in params:
        for r in new_rules.get('buy', []):
            if 'RSI' in r['indicator']:
                r['val'] = params['rsi_buy']
                
    if 'rsi_sell' in params:
        for r in new_rules.get('sell', []):
            if 'RSI' in r['indicator']:
                r['val'] = params['rsi_sell']
                
    # 还可以支持 sma_period 等，这里先支持 RSI
    
    return new_rules