# backend/strategy/engine.py
import pandas as pd
import numpy as np

class BacktestEngine:
    def __init__(self, data_df, initial_cash=0):
        self.data = data_df
        self.initial_cash = initial_cash
        self.history = [] # 记录每一步的状态
        self.strategy = None
        
        # 统计指标
        self.total_slippage_cost = 0.0
        self.current_position = 0.0

    def run(self, strategy_class, **params):
        """
        运行回测
        strategy_class: 策略类
        params: 策略参数 (如 threshold=50)
        """
        # 1. 初始化策略
        self.strategy = strategy_class()
        self.strategy.set_engine(self)
        
        # 注入参数
        for k, v in params.items():
            setattr(self.strategy, k, v)
            
        self.strategy.init()

        # 2. 逐行回测 (Event Driven Loop)
        for index, row in self.data.iterrows():
            # 将 row 转为字典方便访问
            candle = row.to_dict()
            
            # 记录当前行的数据供 execute_order 使用
            self.current_row = candle 
            
            # 执行策略逻辑
            self.strategy.next(candle)
            
            # 记录这一步的结果
            self.history.append({
                "time": candle['time'],
                "type": candle.get('type', 'Unknown'),
                "price": candle['price'],
                "volume": candle['volume'],
                "position": self.current_position,
                "slippage_cost": self.current_slippage_cost, # 本次交易产生的滑点
                "cum_slippage": self.total_slippage_cost
            })
            
        return self.get_results()

    def execute_order(self, target_position):
        """
        执行逻辑修正：
        电力现货/日内场景通常是 Flow Trading（流式交易）。
        这意味着每个小时我们都在市场上成交了 target_position 的量（不管是买还是卖）。
        因此，无论仓位是否变化，只要 volume > 0，就要计算该时刻的 Market Impact Cost。
        """
        row = self.current_row
        
        # --- 修正点：移除 "仓位不变则无成本" 的判断 ---
        # if target_position == self.current_position: ... (删除这行)
        
        # 1. 计算滑点 (Market Impact Model)
        if row['volume'] == 0:
            # 无量时给予惩罚成本
            cost = abs(target_position) * 50.0 
        else:
            # 市场占比
            share = abs(target_position) / row['volume']
            
            # 基础波动率 (取 Std 和 Range 的较大值，防止 Std 失真)
            price_std = row.get('std_price', 0)
            price_range = (row.get('max_price', row['price']) - row.get('min_price', row['price'])) * 0.6
            base_vol = max(price_std, price_range)
            
            # 兜底波动率 (防止完全无波动时滑点为0，实际依然有盘口价差)
            if base_vol < 0.1: 
                base_vol = row['price'] * 0.01
            
            # 冲击系数 (电力市场建议 K=2.0)
            k_factor = 2.0
            
            # 份额惩罚 (非线性)
            impact = np.power(share, 0.8)
            
            # 单价滑点
            unit_slippage = base_vol * k_factor * impact
            
            # 总滑点成本 = 单价滑点 * 本次交易量
            cost = unit_slippage * abs(target_position)
            
        # 2. 更新状态
        self.current_slippage_cost = cost
        self.total_slippage_cost += cost
        self.current_position = target_position

    def get_results(self):
        return pd.DataFrame(self.history)