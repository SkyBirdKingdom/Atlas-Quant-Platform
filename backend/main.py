from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from .database import get_db, Base, engine
from .services import fetcher, analyzer, stats, backtest, market_data, kline_generator # 导入刚才写的服务
from fastapi.middleware.cors import CORSMiddleware # 引入 CORS
import uuid
from contextlib import asynccontextmanager
from . import scheduler
from .models import FetchState
from .strategy.engine import BacktestEngine
from .strategy.strategies import NaiveStrategy, LiquidityRiskStrategy
from sqlalchemy import text
import pandas as pd
from .core.logger import setup_logging
import os
from .utils.time_helper import get_trading_window
from datetime import timezone

# --- 生命周期管理 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # 1. 启动时：开启定时任务
    scheduler.start_scheduler()
    yield
    # 2. 关闭时：停止定时任务
    scheduler.stop_scheduler()

# 自动建表 (为了开发方便)
Base.metadata.create_all(bind=engine)

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 生产环境建议改为具体的 ["http://localhost:5173"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalyzeRequest(BaseModel):
    start_date: str # "2025-12-01"
    end_date: str   # "2025-12-01" (支持跨天)
    area: str = "SE3"
    target_pos: float = 5.0 # 模拟持仓量，用于算滑点

# 定义前端传过来的参数结构
class FetchRequest(BaseModel):
    start_time: str  # "2025-11-01T00:00:00Z"
    end_time: str    # "2025-11-05T00:00:00Z"
    areas: List[str] = ["SE3"] # 默认 SE3，也可以传 ["SE1", "SE2"]

class BacktestRequest(BaseModel):
    start_date: str
    end_date: str
    area: str = "SE3"
    ph_threshold: float = 40
    qh_threshold: float = 10
    base_pos: float = 5.0
    reduced_pos: float = 2.0

class MarketDataRequest(BaseModel):
    start_date: str
    end_date: str
    area: str = "SE3"
    freq: str = "1h" # 1h, 1d

task_status = {}

@app.post("/api/market/kline")
def get_kline_data(req: MarketDataRequest, db: Session = Depends(get_db)):
    data = market_data.get_ohlcv_data(db, req.area, req.start_date, req.end_date, req.freq)
    return {"status": "success", "data": data}

@app.post("/api/backtest/run")
def run_strategy_backtest(req: BacktestRequest, db: Session = Depends(get_db)):
    # 1. 准备数据 (保持不变)
    start_date = req.start_date
    if len(req.end_date) == 10: end_date = f"{req.end_date} 23:59:59"
    else: end_date = req.end_date

    query = text("""
        SELECT 
            delivery_start as time,
            contract_type as type,
            sum(volume) as volume,
            avg(price) as price,
            stddev(price) as std_price,
            max(price) as max_price,
            min(price) as min_price
        FROM trades
        WHERE delivery_area = :area 
          AND delivery_start >= :start 
          AND delivery_start <= :end 
        GROUP BY 1, 2
        ORDER BY 1
    """)
    rows = db.execute(query, {"area": req.area, "start": start_date, "end": end_date}).fetchall()
    
    if not rows:
        return {"status": "empty", "data": {}}
        
    df = pd.DataFrame(rows)
    df['volume'] = df['volume'].fillna(0)
    df['std_price'] = df['std_price'].fillna(0)
    df['max_price'] = df['max_price'].fillna(df['price'])
    df['min_price'] = df['min_price'].fillna(df['price'])

    # 2. 运行 笨策略 (Benchmark)
    engine_naive = BacktestEngine(df)
    res_naive = engine_naive.run(NaiveStrategy, base_pos=req.base_pos)
    
    # 3. 运行 智能策略 (Smart)
    engine_smart = BacktestEngine(df)
    res_smart = engine_smart.run(
        LiquidityRiskStrategy, 
        base_pos=req.base_pos,
        reduced_pos=req.reduced_pos,
        ph_threshold=req.ph_threshold,
        qh_threshold=req.qh_threshold
    )
    
    # --- 4. 关键修改：合并时包含 'slippage_cost' ---
    # 我们需要 'cum_slippage' 画曲线，需要 'slippage_cost' 画柱状图
    merged = pd.merge(
        res_naive[['time', 'cum_slippage', 'slippage_cost']], 
        res_smart[['time', 'cum_slippage', 'position', 'slippage_cost']], 
        on='time', 
        suffixes=('_naive', '_smart')
    )
    
    # 计算累计节省
    merged['saved_cumulative'] = merged['cum_slippage_naive'] - merged['cum_slippage_smart']
    
    # --- 5. 统计汇总 ---
    total_naive = res_naive['slippage_cost'].sum()
    total_smart = res_smart['slippage_cost'].sum()
    
    summary = {
        "total_naive_cost": round(total_naive, 2),
        "total_smart_cost": round(total_smart, 2),
        "total_saved": round(total_naive - total_smart, 2),
        "roi_improvement": round((total_naive - total_smart)/total_naive * 100, 2) if total_naive>0 else 0,
        "downgrade_count": int(len(res_smart[res_smart['position'] < req.base_pos]))
    }
    
    # --- 6. 关键修改：计算单次节省并填充 ---
    chart_data = []
    for _, r in merged.iterrows():
        # 单次节省 = 笨策略当次成本 - 智能策略当次成本
        instant_saved = r['slippage_cost_naive'] - r['slippage_cost_smart']
        
        chart_data.append({
            "time": r['time'].strftime('%Y-%m-%d %H:%M'),
            "cumulative": round(r['saved_cumulative'], 2),
            "saved": round(instant_saved, 2) # <--- 这里原来是 0，现在修复了
        })

    return {
        "status": "success", 
        "data": {
            "summary": summary,
            "chart": chart_data
        }
    }

# --- API 1: 获取数据日历 ---
@app.get("/api/data-availability")
def check_availability(area: str = "SE3", db: Session = Depends(get_db)):
    """返回：{'2025-12-01': 500, '2025-12-02': 1200}"""
    return stats.get_data_calendar(db, area)

# --- API 2: 获取区间热力图 ---
@app.post("/api/analyze/range")
def analyze_range_data(req: AnalyzeRequest, db: Session = Depends(get_db)):
    """
    返回一段时间内的聚合数据，用于热力图和趋势分析
    """
    data = stats.get_heatmap_data(db, req.start_date, req.end_date, req.area)
    return {"status": "success", "data": data}

@app.post("/api/analyze")
def analyze_data(req: AnalyzeRequest, db: Session = Depends(get_db)):
    """
    前端图表调用的核心接口
    返回 PH 和 QH 的统计数据、滑点分析
    """
    try:
        data = analyzer.analyze_liquidity(
            db, 
            req.start_date, 
            req.end_date, 
            req.area, 
            req.target_pos
        )
        return {
            "status": "success",
            "data": data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- API: 触发抓取任务 ---
@app.post("/api/admin/fetch-data")
def trigger_fetch_job(req: FetchRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    接收前端指令，后台异步抓取数据
    """
    # 简单的参数校验
    if req.start_time >= req.end_time:
        raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")
    
    task_id = str(uuid.uuid4())
    task_status[task_id] = "running"

    # 将耗时任务放入后台队列，不阻塞 API 响应
    # 注意：这里我们传入了一个新的 db session 或者让 fetcher 自己管理 session
    # 为了简单，我们让 fetcher 使用一个新的 session，因为 background task 在 response 返回后还在跑
    background_tasks.add_task(run_fetch_wrapper, task_id, req.start_time, req.end_time, req.areas)
    
    return {
        "status": "success", 
        "task_id": task_id
    }

def run_fetch_wrapper(task_id, start, end, areas):
    try:
        run_fetch_in_background(start, end, areas) # 调用之前的逻辑
        task_status[task_id] = "completed"
    except Exception as e:
        task_status[task_id] = "failed"

# --- 辅助函数：后台运行 ---
def run_fetch_in_background(start, end, areas):
    # 手动创建 Session，因为 BackgroundTasks 运行时，原本的依赖注入 Session 可能已经关闭
    from .database import SessionLocal
    db = SessionLocal()
    try:
        fetcher.fetch_data_range(db, start, end, areas)
        print(f"✅ 后台任务完成: {start} -> {end}")
    except Exception as e:
        print(f"❌ 后台任务出错: {e}")
    finally:
        db.close()

@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    return {"status": task_status.get(task_id, "unknown")}

@app.get("/api/system/status")
def check_system_status(db: Session = Depends(get_db)):
    states = db.query(FetchState).all()
    return states


@app.post("/api/admin/generate-kline")
def trigger_kline_gen(req: FetchRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    触发 K 线生成任务 (耗时操作，放后台)
    """
    background_tasks.add_task(run_kline_gen_bg, req.areas, req.start_time, req.end_time)
    return {"status": "success", "message": "K线生成任务已启动"}

def run_kline_gen_bg(areas, start, end):
    from .database import SessionLocal
    db = SessionLocal()
    try:
        for area in areas:
            kline_generator.generate_1min_candles(db, area, start, end)
    finally:
        db.close()

@app.get("/api/admin/logs")
def get_recent_logs(lines: int = 100):
    """
    读取最新的 N 行日志返回给前端
    """
    log_file = os.path.join("logs", "app.log")
    if not os.path.exists(log_file):
        return {"logs": ["暂无日志文件"]}
    
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            # 简单读取最后 N 行 (生产环境可以用更高效的 seek)
            all_lines = f.readlines()
            recent = all_lines[-lines:]
            return {"logs": recent}
    except Exception as e:
        return {"logs": [f"读取日志失败: {str(e)}"]}

@app.get("/api/market/contracts")
def get_daily_contracts(date: str, area: str, db: Session = Depends(get_db)):
    """
    返回合约列表 (严格过滤 PH=60min, QH=15min)
    """
    start = f"{date} 00:00:00"
    end = f"{date} 23:59:59"
    
    # 修改 SQL：查出 delivery_end 以便计算时长
    query = text("""
        SELECT DISTINCT contract_id, delivery_start, delivery_end, contract_type
        FROM trades
        WHERE delivery_area = :area 
          AND delivery_start >= :start 
          AND delivery_start <= :end
        ORDER BY delivery_start
    """)
    
    rows = db.execute(query, {"area": area, "start": start, "end": end}).fetchall()
    
    res = []
    for r in rows:
        # --- 1. 严格时长过滤 ---
        duration_seconds = (r.delivery_end - r.delivery_start).total_seconds()
        duration_minutes = duration_seconds / 60
        
        # 允许 1分钟以内的误差 (防止浮点数精度问题)
        is_ph = abs(duration_minutes - 60) < 1
        is_qh = abs(duration_minutes - 15) < 1
        
        if not (is_ph or is_qh):
            continue # 跳过非标准合约 (如 Block 合约)

        # 修正类型标签 (防止数据库存错)
        real_type = "PH" if is_ph else "QH"

        # --- 2. 计算理论开收盘时间 ---
        open_dt, close_dt = get_trading_window(r.delivery_start)
        
        delivery_str = r.delivery_start.strftime("%H:%M")
        res.append({
            "contract_id": r.contract_id,
            "label": f"{delivery_str} ({real_type})",
            "type": real_type,
            "delivery_time": r.delivery_start.strftime("%Y-%m-%d %H:%M"),
            "delivery_end": r.delivery_end.strftime("%Y-%m-%d %H:%M"),
            "open_ts": int(open_dt.timestamp()),
            "close_ts": int(close_dt.timestamp())
        })
    return res

# 2. 获取指定合约的 K 线数据
@app.get("/api/market/candles/{contract_id}")
def get_contract_candles(contract_id: str, db: Session = Depends(get_db)):
    from .models import MarketCandle
    
    candles = db.query(MarketCandle).filter(
        MarketCandle.contract_id == contract_id
    ).order_by(MarketCandle.timestamp).all()
    
    data = []
    for c in candles:
        # === 核心修复 ===
        # 1. c.timestamp 是 Naive 的 (例如 13:00)
        # 2. .replace(tzinfo=timezone.utc) 把它标记为 UTC (UTC 13:00)
        # 3. .timestamp() 才会算出正确的时间戳
        ts = int(c.timestamp.replace(tzinfo=timezone.utc).timestamp())
        
        data.append({
            "time": ts, 
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "vwap": c.vwap
        })
    return data

@app.get("/api/debug/trades/{area}/{contract_id}")
def get_raw_trades_for_debug(area: str, contract_id: str, db: Session = Depends(get_db)):
    """
    【调试专用】获取指定合约的所有原始成交记录
    """
    query = text("""
        SELECT trade_id, trade_time, price, volume, delivery_area
        FROM trades
        WHERE contract_id = :cid and delivery_area = :area
        ORDER BY trade_time ASC
    """)
    
    rows = db.execute(query, {"cid": contract_id, "area": area}).fetchall()
    
    data = []
    for r in rows:
        data.append({
            "trade_id": r.trade_id,
            "time_utc": r.trade_time.strftime('%Y-%m-%d %H:%M:%S'), # 显示 UTC
            "price": r.price,
            "volume": r.volume,
            "area": r.delivery_area
        })
    return data