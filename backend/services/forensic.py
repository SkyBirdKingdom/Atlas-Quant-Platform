import os
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from ..models import Trade, OrderFlowTick, OrderContract

class MarketForensics:
    """
    市场微观结构取证分析器 (混合存储版)
    支持自动路由：热数据(DB) / 冷数据(Parquet)
    """

    def __init__(self, db: Session):
        self.db = db
        self.base_data_dir = "data/order_flow" # 与 storage.py 保持一致

    def detect_price_anomalies(self, area: str, start_date: str, end_date: str, threshold_pct: float = 0.05, target_contract_id: Optional[str] = None):
        """
        【第一步】宏观扫描：寻找价格异常波动的时间窗口
        (逻辑保持不变，因为 Trade 数据通常全量在 DB 中，或者 Trade 数据量小一直存 DB)
        """
        query = self.db.query(Trade.trade_time, Trade.price, Trade.contract_id)\
            .filter(
                Trade.delivery_area == area,
                Trade.trade_time >= start_date,
                Trade.trade_time <= end_date
            )
        
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
        
        # 按 5分钟 分组
        ohlc = df.groupby([pd.Grouper(freq='5T'), 'contract'])['price'].agg(['first', 'max', 'min', 'last']).reset_index()
        
        anomalies = []
        for _, row in ohlc.iterrows():
            open_px = row['first']
            if open_px <= 0: continue
            
            pump_pct = (row['max'] - open_px) / open_px
            dump_pct = (row['min'] - open_px) / open_px
            
            anomaly_type = None
            change_pct = 0
            
            if pump_pct > threshold_pct:
                anomaly_type = "Pump"
                change_pct = pump_pct
            elif dump_pct < -threshold_pct:
                anomaly_type = "Dump"
                change_pct = dump_pct
            
            if anomaly_type:
                anomalies.append({
                    "contract_id": row['contract'],
                    "start_time": row['time'],
                    "end_time": row['time'] + timedelta(minutes=5),
                    "open": open_px,
                    "high": row['max'] if anomaly_type == "Pump" else row['min'],
                    "change_pct": round(change_pct * 100, 2),
                    "type": anomaly_type
                })
                
        return sorted(anomalies, key=lambda x: abs(x['change_pct']), reverse=True)

    def _load_ticks(self, contract_id: str, start_time: datetime, end_time: datetime) -> List[Dict]:
        """
        【核心逻辑】智能加载数据 (Parquet 优先 -> DB 兜底)
        """
        ticks_data = []
        
        # 1. 获取合约元数据以确定文件路径
        contract = self.db.query(OrderContract).filter(OrderContract.contract_id == contract_id).first()
        
        if contract and contract.delivery_date_utc:
            area = contract.delivery_area
            date_str = contract.delivery_date_utc.strftime('%Y-%m-%d')
            file_path = os.path.join(self.base_data_dir, area, date_str, f"{contract_id}.parquet")
            
            # --- 尝试加载冷数据 (Parquet) ---
            if os.path.exists(file_path):
                try:
                    df = pd.read_parquet(file_path)
                    
                    # 确保时间列为 datetime 且带时区
                    if 'updated_time' in df.columns:
                        df['updated_time'] = pd.to_datetime(df['updated_time'])
                        # 如果 parquet 里存的是 naive 时间，假定为 UTC
                        if df['updated_time'].dt.tz is None:
                            df['updated_time'] = df['updated_time'].dt.tz_localize('UTC')
                        
                        # 确保 start_time/end_time 带时区
                        if start_time.tzinfo is None: start_time = start_time.replace(tzinfo=timezone.utc)
                        if end_time.tzinfo is None: end_time = end_time.replace(tzinfo=timezone.utc)

                        # 过滤时间段
                        mask = (df['updated_time'] >= start_time) & (df['updated_time'] <= end_time)
                        filtered_df = df[mask]
                        
                        # 转为字典列表
                        ticks_data = filtered_df.to_dict('records')
                        return ticks_data
                        
                except Exception as e:
                    print(f"[Forensic] 读取 Parquet 失败，尝试查库: {e}")

        # --- 降级加载热数据 (DB) ---
        # 修正字段名 timestamp -> updated_time
        db_ticks = self.db.query(OrderFlowTick).filter(
            OrderFlowTick.contract_id == contract_id,
            OrderFlowTick.updated_time >= start_time,
            OrderFlowTick.updated_time <= end_time
        ).order_by(OrderFlowTick.updated_time).all()
        
        # 将 ORM 对象转为字典，与 Parquet 格式统一
        for t in db_ticks:
            ticks_data.append({
                "volume": t.volume,
                "price": t.price,
                "side": t.side,
                "is_deleted": t.is_deleted,
                "updated_time": t.updated_time,
                "priority_time": t.priority_time
            })
            
        return ticks_data

    def analyze_microstructure(self, contract_id: str, start_time: datetime, end_time: datetime):
        """
        【第二步】微观分析
        """
        # 使用通用加载器获取数据
        ticks = self._load_ticks(contract_id, start_time, end_time)
        
        if not ticks:
            return {
                "total_volume": 0,
                "aggressive_buy_ratio": 0,
                "spoofing_ratio_buy": 0,
                "spoofing_ratio_sell": 0,
                "large_orders": [],
                "conclusion": "该时段无订单流数据(DB/File均未找到)"
            }

        metrics = {
            "total_volume": 0,
            "limit_buy_added": 0,      
            "limit_buy_canceled": 0,   
            "limit_sell_added": 0,
            "limit_sell_canceled": 0,
            "large_orders": []         
        }
        
        large_order_threshold = 20 
        
        for t in ticks:
            # 兼容处理：Parquet读出来是字典，DB读出来转成了字典
            vol = t.get('volume', 0)
            price = t.get('price', 0)
            side = t.get('side', 'UNKNOWN')
            is_deleted = t.get('is_deleted', False)
            # 处理时间格式 (可能是 Timestamp 对象或 datetime)
            ts = t.get('updated_time')
            time_str = ts.strftime('%H:%M:%S.%f') if hasattr(ts, 'strftime') else str(ts)

            # 3. 撤单分析 (Deleted)
            if is_deleted:
                if side == 'BUY':
                    metrics["limit_buy_canceled"] += vol
                else:
                    metrics["limit_sell_canceled"] += vol
                    
                if vol >= large_order_threshold:
                    metrics["large_orders"].append({
                        "time": time_str,
                        "action": "CANCELED",
                        "side": side,
                        "price": price,
                        "volume": vol
                    })
            
            # 2. 挂单/修改分析 (非删除)
            else:
                if side == 'BUY':
                    metrics["limit_buy_added"] += vol
                else:
                    metrics["limit_sell_added"] += vol
                
                if vol >= large_order_threshold:
                    metrics["large_orders"].append({
                        "time": time_str,
                        "action": "PLACED",
                        "side": side,
                        "price": price,
                        "volume": vol
                    })

        # --- 计算指标 ---
        spoof_buy = (metrics["limit_buy_canceled"] / metrics["limit_buy_added"]) if metrics["limit_buy_added"] > 0 else 0
        spoof_sell = (metrics["limit_sell_canceled"] / metrics["limit_sell_added"]) if metrics["limit_sell_added"] > 0 else 0
        
        metrics["spoofing_ratio_buy"] = round(spoof_buy * 100, 2)
        metrics["spoofing_ratio_sell"] = round(spoof_sell * 100, 2)
        metrics["aggressive_buy_ratio"] = 0 # 需结合 Trade 表计算，暂置 0
        
        # 结论
        conclusion = []
        if spoof_buy > 0.8 and metrics["limit_buy_added"] > 50:
            conclusion.append("⚠️ 买方严重诱多 (Spoofing Buy): 挂多撤多。")
        if spoof_sell > 0.8 and metrics["limit_sell_added"] > 50:
            conclusion.append("⚠️ 卖方严重诱空 (Spoofing Sell): 挂多撤多。")
        if any(o['action'] == 'CANCELED' for o in metrics['large_orders']):
             conclusion.append("🚨 检测到大额订单撤单。")
        if not conclusion:
            conclusion.append("✅ 订单流相对平稳。")

        metrics["conclusion"] = " ".join(conclusion)
        return metrics