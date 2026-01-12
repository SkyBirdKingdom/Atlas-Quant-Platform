# backend/services/forensic.py
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from ..models import Trade, OrderFlowTick

class MarketForensics:
    """
    市场微观结构取证分析器
    用于检测市场操纵、异常波动及主力行为
    """

    def __init__(self, db: Session):
        self.db = db

    def detect_price_anomalies(self, area: str, start_date: str, end_date: str, threshold_pct: float = 0.05, target_contract_id: Optional[str] = None):
        """
        【第一步】宏观/定点扫描：寻找价格异常波动的时间窗口
        :param threshold_pct: 价格突变阈值 (默认 0.05 即 5%)
        :param target_contract_id: (可选) 如果指定，则只分析该合约
        """
        # 1. 构建查询条件
        query = self.db.query(Trade.trade_time, Trade.price, Trade.contract_id)\
            .filter(
                Trade.delivery_area == area,
                Trade.trade_time >= start_date,
                Trade.trade_time <= end_date
            )
        
        # 如果指定了合约，增加过滤条件
        if target_contract_id:
            query = query.filter(Trade.contract_id == target_contract_id)
            
        trades = query.order_by(Trade.trade_time).all()
            
        if not trades:
            return []

        df = pd.DataFrame([{
            'time': t.trade_time, 
            'price': t.price, 
            'contract': t.contract_id
        } for t in trades])
        
        if df.empty:
            return []
            
        df.set_index('time', inplace=True)
        
        # 按合约和 5分钟 分组计算 OHLC
        ohlc = df.groupby([pd.Grouper(freq='5T'), 'contract'])['price'].agg(['first', 'max', 'min', 'last']).reset_index()
        
        anomalies = []
        for _, row in ohlc.iterrows():
            open_px = row['first']
            high_px = row['max']
            low_px = row['min']
            
            if open_px <= 0: continue
            
            # 计算最大振幅 (无论是向上拉升还是向下砸盘)
            # 拉升幅度
            pump_pct = (high_px - open_px) / open_px
            # 砸盘幅度
            dump_pct = (low_px - open_px) / open_px
            
            # 判定类型
            anomaly_type = None
            change_pct = 0
            
            # 如果指定了合约，我们对阈值放宽，记录所有波动；或者严格遵守阈值
            # 这里逻辑：如果超过阈值，记录下来
            if pump_pct > threshold_pct:
                anomaly_type = "Pump"
                change_pct = pump_pct
            elif dump_pct < -threshold_pct:
                anomaly_type = "Dump"
                change_pct = dump_pct
            
            # 如果是定点分析 (有 target_contract_id)，即使没超过阈值，
            # 只要有一定波动(比如1%)也可能想看，可以根据需求调整逻辑。
            # 目前逻辑：必须超过 threshold_pct 才返回
            if anomaly_type:
                anomalies.append({
                    "contract_id": row['contract'],
                    "start_time": row['time'],
                    "end_time": row['time'] + timedelta(minutes=5),
                    "open": open_px,
                    "high": high_px if anomaly_type == "Pump" else low_px,
                    "change_pct": round(change_pct * 100, 2),
                    "type": anomaly_type
                })
                
        # 按波动幅度降序排列
        return sorted(anomalies, key=lambda x: abs(x['change_pct']), reverse=True)

    def analyze_microstructure(self, contract_id: str, start_time: datetime, end_time: datetime):
        """
        【第二步】微观分析：深入分析指定窗口内的订单流行为
        :param contract_id: 目标合约
        :param start_time: 窗口开始时间
        :param end_time: 窗口结束时间
        """
        # 拉取该时间段的所有 Tick (挂单、撤单、成交)
        # 注意：这里需要 OrderFlowTick 表中有数据 (历史归档已完成)
        ticks = self.db.query(OrderFlowTick).filter(
            OrderFlowTick.contract_id == contract_id,
            OrderFlowTick.timestamp >= start_time,
            OrderFlowTick.timestamp <= end_time
        ).order_by(OrderFlowTick.timestamp).all()
        
        if not ticks:
            return {"status": "no_data", "msg": "该时段无订单流数据，请检查归档状态"}

        metrics = {
            "total_volume": 0,
            "buy_aggressor_vol": 0,    
            "sell_aggressor_vol": 0,   
            "limit_buy_added": 0,      
            "limit_buy_canceled": 0,   
            "limit_sell_added": 0,
            "limit_sell_canceled": 0,
            "large_orders": []         
        }
        
        # 动态定义大单阈值 (简单起见，假设 > 20MW 算大单，实际应根据该合约平均单量计算)
        large_order_threshold = 20 
        
        for t in ticks:
            vol = t.volume
            # 1. 成交分析
            if t.type == 'TRADE': # 或者 t.side 为空但有成交
                # 有些历史数据可能没有标记 type='TRADE'，需结合 processor 逻辑
                # 假设 processor 已经标记好了 type
                metrics["total_volume"] += vol
                # 历史归档数据可能没有 aggressor_side (Stream才有)，这里做个兼容
                # 如果没有，只能看 side
                side = t.side or 'UNKNOWN'
                # 注意：Trade 的 Side 并不直接等于 Aggressor，需谨慎。
                # 如果数据库没存 aggressor，只能略过此指标或近似估算
                pass
            
            # 2. 挂单分析
            elif t.type in ['NEW', 'UPDATE']:
                if t.side == 'BUY':
                    metrics["limit_buy_added"] += vol
                else:
                    metrics["limit_sell_added"] += vol
                
                # 记录大单挂入
                if vol >= large_order_threshold:
                    metrics["large_orders"].append({
                        "time": t.timestamp.strftime('%H:%M:%S.%f'),
                        "action": "PLACED",
                        "side": t.side,
                        "price": t.price,
                        "volume": vol
                    })

            # 3. 撤单分析 (Deleted)
            elif t.type == 'CANCEL' or t.is_deleted:
                if t.side == 'BUY':
                    metrics["limit_buy_canceled"] += vol
                else:
                    metrics["limit_sell_canceled"] += vol
                    
                # 记录大单撤销 (这就是 Spoofing 的铁证)
                if vol >= large_order_threshold:
                    metrics["large_orders"].append({
                        "time": t.timestamp.strftime('%H:%M:%S.%f'),
                        "action": "CANCELED",
                        "side": t.side,
                        "price": t.price,
                        "volume": vol
                    })

        # --- 计算衍生指标 ---
        
        # 虚假买盘比率 (Spoofing Ratio Buy) = 撤掉的买单 / 挂出的买单
        spoof_buy = (metrics["limit_buy_canceled"] / metrics["limit_buy_added"]) if metrics["limit_buy_added"] > 0 else 0
        
        # 虚假卖盘比率 (Spoofing Ratio Sell)
        spoof_sell = (metrics["limit_sell_canceled"] / metrics["limit_sell_added"]) if metrics["limit_sell_added"] > 0 else 0
        
        metrics["spoofing_ratio_buy"] = round(spoof_buy * 100, 2)
        metrics["spoofing_ratio_sell"] = round(spoof_sell * 100, 2)
        
        # 结论生成
        conclusion = []
        if spoof_buy > 0.8 and metrics["limit_buy_added"] > 50:
            conclusion.append("⚠️ 买方严重虚假挂单 (Spoofing Buy): 挂多撤多，意图诱多或托底。")
        if spoof_sell > 0.8 and metrics["limit_sell_added"] > 50:
            conclusion.append("⚠️ 卖方严重虚假挂单 (Spoofing Sell): 挂多撤多，意图打压价格。")
        
        # 检查是否有大单“秒撤” (Flash Cancel)
        # 这里只是简单展示，实际可以比对大单挂入和撤销的时间差
        if any(o['action'] == 'CANCELED' for o in metrics['large_orders']):
             conclusion.append("🚨 检测到大额订单撤单行为。")
             
        if not conclusion:
            conclusion.append("✅ 订单流行为相对平稳。")

        metrics["conclusion"] = " ".join(conclusion)
        
        return metrics