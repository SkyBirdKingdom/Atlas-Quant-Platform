import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..models import Trade, FetchState

logger = logging.getLogger("NordPoolFetcher")

# 配置
AUTO_AREAS = ["SE1", "SE2", "SE3", "SE4"]
API_URL = "https://data-api.nordpoolgroup.com/api/v2/Intraday/Trades/ByDeliveryStart"

# --- 1. 网络层：带自动重试的 API 请求 ---

# === 配置区 ===
# 首次运行时，如果没有历史记录，从这个时间点开始抓取
# 注意：格式必须是 ISO 8601
INITIAL_START_DATE = "2025-01-01T00:00:00Z"

def get_token():
    token_url = "https://sts.nordpoolgroup.com/connect/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Authorization": "Basic Y2xpZW50X21hcmtldGRhdGFfYXBpOmNsaWVudF9tYXJrZXRkYXRhX2FwaQ=="}
    params = {"grant_type": "password", "scope": "marketdata_api", "username": "API_DATA_GreenVoltisSwedenAB", "password": "6meGT1)=WX85(aRm2b"}
    resp = requests.post(token_url, headers=headers, data=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get("access_token")

# 使用装饰器处理重试：
# - 最多重试 5 次
# - 等待时间指数增长 (2s, 4s, 8s...)，防止把 NordPool 冲垮
# - 只在遇到 RequestException (网络错, 500, 502) 时重试
@retry(
    stop=stop_after_attempt(5), 
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type(requests.RequestException)
)
def fetch_api_chunk(token, area, start_str, end_str):
    params = {"deliveryStartFrom": start_str, "deliveryStartTo": end_str, "areas": area}
    headers = {"accept": "application/json", "authorization": f"Bearer {token}"}
    
    resp = requests.get(API_URL, params=params, headers=headers, timeout=30)
    
    # 特殊处理：如果 Token 过期 (401)，抛出错误让外层刷新 Token
    if resp.status_code == 401:
        raise PermissionError("Token expired")
    
    resp.raise_for_status() # 非 200 抛出异常，触发重试
    return resp.json()

# --- 2. 数据处理与存储 ---

def flatten_and_parse(raw_data, area):
    rows = []
    volume_unit = raw_data.get('volumeUnit')
    
    for contract in raw_data.get('contracts', []) or []:
        base = {
            'contractId': contract.get('contractId'),
            'contractName': contract.get('contractName'),
            'deliveryStart': contract.get('deliveryStart'),
            'deliveryEnd': contract.get('deliveryEnd'),
        }
        for trade in contract.get('trades', []) or []:
            # 过滤：只保留 belongs to area 的数据
            # 或者是 legs 里包含该 area
            legs = trade.get('legs') or []
            target_area_found = False
            
            # 如果没有 legs (单边交易?) 或者 legs 里有当前区域
            if not legs:
                target_area_found = True # 假设 API 筛选过了
            else:
                for leg in legs:
                    if leg.get('deliveryArea') == area:
                        target_area_found = True
                        break
            
            if target_area_found:
                rows.append({
                    **base,
                    'tradeId': trade.get('tradeId'),
                    'tradeTime': trade.get('tradeTime'),
                    'price': trade.get('price'),
                    'volume': trade.get('volume'),
                    'deliveryArea': area 
                })
    return rows

def save_chunk_to_db(db: Session, data_list: list):
    if not data_list: return
    
    df = pd.DataFrame(data_list)
    for col in ['deliveryStart', 'deliveryEnd', 'tradeTime']:
        df[col] = pd.to_datetime(df[col], format='mixed')

    df['duration_minutes'] = (df['deliveryEnd'] - df['deliveryStart']).dt.total_seconds() / 60
    conditions = [(abs(df['duration_minutes']-60)<1), (abs(df['duration_minutes']-15)<1)]
    df['contract_type'] = np.select(conditions, ['PH', 'QH'], default='Other')

    records = df.to_dict(orient='records')
    
    # 映射字段名
    db_records = []
    for r in records:
        db_records.append({
            "trade_id": r['tradeId'],
            "contract_id": r['contractId'],
            "contract_name": r['contractName'],
            "price": r['price'],
            "volume": r['volume'],
            "delivery_area": r['deliveryArea'],
            "delivery_start": r['deliveryStart'],
            "delivery_end": r['deliveryEnd'],
            "trade_time": r['tradeTime'],
            "duration_minutes": r['duration_minutes'],
            "contract_type": r['contract_type']
        })

    stmt = insert(Trade).values(db_records)
    stmt = stmt.on_conflict_do_nothing(index_elements=['trade_id'])
    db.execute(stmt)

# --- 3. 状态管理 ---

def update_fetch_state(db: Session, area: str, last_time=None, status="running", error=None):
    state = db.query(FetchState).filter(FetchState.area == area).first()
    if not state:
        state = FetchState(area=area)
        db.add(state)
    
    if last_time:
        state.last_fetched_time = last_time
    
    state.status = status
    state.updated_at = datetime.utcnow()
    if error:
        state.last_error = str(error)[:500] # 截断错误信息防止太长
    else:
        state.last_error = None # 清除错误
        
    db.commit()

# --- 4. 主同步逻辑 (增强版) ---

def sync_area_logic(db: Session, area: str):
    """
    修正后的同步逻辑：
    1. 历史数据：推进 Checkpoint。
    2. 活跃数据：每次强制重刷，不推进 Checkpoint。
    """
    # 获取数据库里的进度（这是“已归档”的时间线）
    state = db.query(FetchState).filter(FetchState.area == area).first()
    
    if state and state.last_fetched_time:
        # 场景 A: 不是第一次运行，接着上次的进度跑
        archived_time = state.last_fetched_time
    else:
        # 场景 B: 第一次运行 (冷启动)，使用配置的初始时间
        # 去掉 'Z' 因为 datetime.fromisoformat 在某些 Python 版本对 Z 支持不好，或者统一用 replace处理
        archived_time = datetime.fromisoformat(INITIAL_START_DATE.replace('Z', ''))
        logger.info(f"[{area}] 首次初始化，从配置时间开始: {archived_time}")
    
    # 定义“现在”和“未来边界”
    now = datetime.utcnow()
    # 活跃窗口：Nord Pool 日内通常最多开放到明天 (约 +36~48 小时)
    future_limit = now + timedelta(hours=48)
    
    # === 第一阶段：追赶历史 (Backfill) ===
    # 这里的目标是把 Checkpoint 推进到 "Now"
    # 我们认为：DeliveryTime < Now 的数据，基本已经稳定（虽然理论上交割前都能交易，但为了简化，我们假设"过去"的时间段只需推进）
    # 更严谨的做法是：Checkpoint 只能推进到 "Now - 交付周期时长"，比如 Now - 2小时
    
    # 设定一个“安全归档线”，比如 2 小时前。在此之前的交付时段，我们认为数据不会再变了，可以更新 Checkpoint。
    safe_archive_line = now - timedelta(hours=2)
    
    curr = archived_time
    token = get_token()
    
    # 1. 循环推进历史进度
    while curr < safe_archive_line:
        # 每次步进 12 小时
        chunk_end = min(curr + timedelta(hours=12), safe_archive_line)
        
        t_start = curr.strftime('%Y-%m-%dT%H:%M:%SZ')
        t_end = chunk_end.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        try:
            logger.info(f"[{area}] 📥 补录历史: {t_start} -> {t_end}")
            
            # API 请求与入库
            try:
                raw = fetch_api_chunk(token, area, t_start, t_end)
            except PermissionError:
                token = get_token()
                raw = fetch_api_chunk(token, area, t_start, t_end)
            
            data = flatten_and_parse(raw, area)
            if data:
                save_chunk_to_db(db, data)
            
            # 关键：历史数据抓完一段，更新一次数据库 Checkpoint
            update_fetch_state(db, area, last_time=chunk_end, status="running")
            curr = chunk_end
            
        except Exception as e:
            logger.error(f"[{area}] 历史补录失败: {e}")
            update_fetch_state(db, area, status="error", error=str(e))
            return # 历史都挂了，后面就别跑了
            
    # === 第二阶段：刷新活跃窗口 (Active Window) ===
    # 从 "安全归档线" 一直抓到 "未来边界"
    # 这部分数据绝对【不能】更新 Checkpoint，因为下一小时还要再来抓一遍新成交的
    
    active_start = curr # 接着上面的进度
    logger.info(f"[{area}] 🔄 刷新活跃窗口: {active_start.strftime('%Y-%m-%dT%H:%M')} -> {future_limit.strftime('%Y-%m-%dT%H:%M')}")
    
    while active_start < future_limit:
        chunk_end = min(active_start + timedelta(hours=12), future_limit)
        
        t_start = active_start.strftime('%Y-%m-%dT%H:%M:%SZ')
        t_end = chunk_end.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        try:
            # API 请求
            try:
                raw = fetch_api_chunk(token, area, t_start, t_end)
            except PermissionError:
                token = get_token()
                raw = fetch_api_chunk(token, area, t_start, t_end)
            
            data = flatten_and_parse(raw, area)
            
            # 入库 (利用数据库的 ON CONFLICT DO NOTHING 去重)
            # 虽然我们重复抓取了，但数据库里已有的 TradeID 会被忽略，只有新产生的 TradeID 会被插入
            if data:
                save_chunk_to_db(db, data)
                logger.info(f"[{area}] 活跃窗口更新: 抓取 {len(data)} 条")
            
            # 注意：这里【不调用】update_fetch_state 来更新 last_time
            active_start = chunk_end
            
        except Exception as e:
            logger.error(f"[{area}] 活跃窗口刷新失败: {e}")
            # 活跃窗口偶尔失败不影响大局，记录错误即可
            update_fetch_state(db, area, status="warning", error=f"Active window error: {e}")
            break

    # 全部跑完，状态标为 OK
    update_fetch_state(db, area, status="ok")

def sync_all_areas(db: Session):
    """
    入口函数：遍历所有区域
    """
    logger.info("⏰ 启动定时同步...")
    for area in AUTO_AREAS:
        try:
            sync_area_logic(db, area)
        except Exception as e:
            logger.error(f"❌ [{area}] 任务中断: {e}")
            # 这里 catch 住，保证 SE3 挂了不影响 SE4 继续跑
            continue
    logger.info("✅ 定时同步结束")