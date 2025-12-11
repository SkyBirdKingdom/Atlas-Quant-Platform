import sys
import os
import requests
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from time import sleep
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

# --- 环境设置 ---
# 将 backend 目录加入路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import engine, Base, SessionLocal
from backend.models import Trade, FetchState

# --- 配置区域 ---
CONFIG = {
    # 要抓取的区域列表
    "AREAS": ["SE1", "SE2", "SE3", "SE4"],
    
    # 如果数据库里没有记录，默认从这个时间开始抓
    "DEFAULT_START_TIME": "2025-11-01T00:00:00Z",
    
    # 每次往后预抓取多少天（NordPool日内通常开放到明天）
    "FETCH_UNTIL_DAYS_AHEAD": 2 
}

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s'
)
logger = logging.getLogger("AutoFetcher")

# --- API 工具函数 (复用你的逻辑) ---
MAX_RETRIES = 3
RETRY_DELAY = 2

def get_token(retries=MAX_RETRIES):
    token_url = "https://sts.nordpoolgroup.com/connect/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": "Basic Y2xpZW50X21hcmtldGRhdGFfYXBpOmNsaWVudF9tYXJrZXRkYXRhX2FwaQ=="}
    params = {"grant_type": "password", "scope": "marketdata_api", "username": "API_DATA_GreenVoltisSwedenAB", "password": "6meGT1)=WX85(aRm2b"}
    
    for attempt in range(retries):
        try:
            resp = requests.post(token_url, headers=headers, data=params, timeout=60)
            if resp.status_code == 200: return resp.json().get("access_token")
        except Exception: pass
        sleep(RETRY_DELAY)
    raise Exception("无法获取Token")

def get_trades_api(token, start, end, area):
    url = "https://data-api.nordpoolgroup.com/api/v2/Intraday/Trades/ByDeliveryStart"
    headers = {"accept": "application/json", "authorization": f"Bearer {token}"}
    params = {"deliveryStartFrom": start, "deliveryStartTo": end, "areas": area}
    
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    if resp.status_code == 200: return resp.json()
    if resp.status_code == 401: raise ValueError("TokenExpired") # 抛出特定错误以便外层处理
    resp.raise_for_status()

def flatten_data(raw_data):
    """复用你的展平逻辑，稍作精简"""
    rows = []
    volume_unit = raw_data.get('volumeUnit')
    for contract in raw_data.get('contracts', []) or []:
        base = {
            'volumeUnit': volume_unit,
            'contractId': contract.get('contractId'),
            'contractName': contract.get('contractName'),
            'priceUnit': contract.get('priceUnit'),
            'deliveryStart': contract.get('deliveryStart'),
            'deliveryEnd': contract.get('deliveryEnd'),
        }
        for trade in contract.get('trades', []) or []:
            trade_base = base.copy()
            trade_base.update({
                'tradeId': trade.get('tradeId'),
                'tradeTime': trade.get('tradeTime'),
                'price': trade.get('price'),
                'volume': trade.get('volume'),
                'deliveryArea': None # 默认为空
            })
            
            legs = trade.get('legs') or []
            if not legs:
                rows.append(trade_base)
            else:
                for leg in legs:
                    row = trade_base.copy()
                    row['deliveryArea'] = leg.get('deliveryArea')
                    rows.append(row)
    return rows

# --- 数据库操作核心 ---

def get_last_checkpoint(db: Session, area: str):
    """获取某个区域上次抓取到的时间"""
    state = db.query(FetchState).filter(FetchState.area == area).first()
    if state:
        return state.last_fetched_time
    return None

def update_checkpoint(db: Session, area: str, last_time: datetime):
    """更新断点记录"""
    state = db.query(FetchState).filter(FetchState.area == area).first()
    if not state:
        state = FetchState(area=area)
        db.add(state)
    state.last_fetched_time = last_time
    state.updated_at = datetime.now()
    db.commit()

def save_batch_to_db(db: Session, data_list: list):
    """
    批量写入数据库，使用 ON CONFLICT DO NOTHING 忽略重复主键
    """
    if not data_list: return

    # 1. 转为 DataFrame 进行清洗 (PH/QH 计算)
    df = pd.DataFrame(data_list)
    df['deliveryStart'] = pd.to_datetime(df['deliveryStart'])
    df['deliveryEnd'] = pd.to_datetime(df['deliveryEnd'])
    df['tradeTime'] = pd.to_datetime(df['tradeTime'])
    
    # 计算 PH/QH
    df['duration_minutes'] = (df['deliveryEnd'] - df['deliveryStart']).dt.total_seconds() / 60
    conditions = [
        (abs(df['duration_minutes'] - 60) < 1),
        (abs(df['duration_minutes'] - 15) < 1)
    ]
    df['contract_type'] = np.select(conditions, ['PH', 'QH'], default='Other')

    # 2. 映射字段名 (DataFrame -> DB Columns)
    db_records = []
    for _, row in df.iterrows():
        record = {
            "trade_id": row['tradeId'],
            "price": row['price'],
            "volume": row['volume'],
            "delivery_area": row['deliveryArea'],
            "delivery_start": row['deliveryStart'],
            "delivery_end": row['deliveryEnd'],
            "trade_time": row['tradeTime'],
            "duration_minutes": row['duration_minutes'],
            "contract_type": row['contract_type']
        }
        db_records.append(record)

    # 3. 执行 Upsert (Insert on Conflict do nothing)
    # 这是一个高效的 SQL 操作
    stmt = insert(Trade).values(db_records)
    stmt = stmt.on_conflict_do_nothing(index_elements=['trade_id'])
    
    db.execute(stmt)
    db.commit()

# --- 主逻辑 ---

def process_area(area: str, db: Session, token: str):
    # 1. 确定时间范围
    last_time = get_last_checkpoint(db, area)
    
    if last_time:
        start_dt = last_time
    else:
        start_dt = datetime.fromisoformat(CONFIG["DEFAULT_START_TIME"].replace('Z', ''))
        # 确保是 UTC
        if start_dt.tzinfo is None:
            pass # 假设配置就是 UTC
            
    # 结束时间：当前时间 + 预取天数
    end_dt = datetime.utcnow() + timedelta(days=CONFIG["FETCH_UNTIL_DAYS_AHEAD"])
    
    logger.info(f">>> 开始处理区域: {area} | 起点: {start_dt} | 终点: {end_dt}")

    # 2. 按 12 小时切片循环
    current_chunk_start = start_dt
    
    while current_chunk_start < end_dt:
        current_chunk_end = min(current_chunk_start + timedelta(hours=12), end_dt)
        
        # 格式化为 API 需要的 ISO 字符串
        t_str_start = current_chunk_start.strftime('%Y-%m-%dT%H:%M:%SZ')
        t_str_end = current_chunk_end.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        try:
            logger.info(f"[{area}] 获取 {t_str_start} -> {t_str_end}")
            
            # API 请求
            try:
                raw_data = get_trades_api(token, t_str_start, t_str_end, area)
            except ValueError: # TokenExpired
                token = get_token() # 刷新 Token
                raw_data = get_trades_api(token, t_str_start, t_str_end, area)
            
            # 数据处理
            flat_data = flatten_data(raw_data)
            
            # 数据入库 (如果没有数据也要更新 checkpoint，防止死循环)
            if flat_data:
                # 过滤出真正属于当前 area 的数据 (因为 legs 可能包含 cross border)
                # 策略：只要 trade 的 leg 中包含当前 area，或者 api 就是查的当前 area，这里简单处理
                # NordPool API如果查 SE3，返回的 legs 里可能一个是 SE3 一个是 DK1。我们只存 SE3 的记录。
                filtered_data = [d for d in flat_data if d['deliveryArea'] == area]
                save_batch_to_db(db, filtered_data)
                logger.info(f"[{area}] 存入 {len(filtered_data)} 条记录")
            
            # 更新断点 (Checkpoint)
            update_checkpoint(db, area, current_chunk_end)
            
            # 步进
            current_chunk_start = current_chunk_end
            sleep(0.5) # 稍微礼貌一点
            
        except Exception as e:
            logger.error(f"[{area}] 处理出错: {e}")
            sleep(5) # 出错多歇会儿
            # 出错不更新 checkpoint，下次重试
            # 如果是致命错误可以选择 break
            break

def main():
    # 1. 初始化数据库表 (确保新加的 FetchState 表存在)
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        token = get_token()
        
        for area in CONFIG["AREAS"]:
            process_area(area, db, token)
            
    finally:
        db.close()
        logger.info("所有任务执行完毕")

if __name__ == "__main__":
    main()