from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from .database import get_db, Base, engine
from .services import fetcher, analyzer, stats, backtest, market_data, kline_generator, feature_engine, live_runner, optimizer # 导入刚才写的服务
from fastapi.middleware.cors import CORSMiddleware # 引入 CORS
import uuid
from contextlib import asynccontextmanager
from . import scheduler
from .models import FetchState
from sqlalchemy import text
import pandas as pd
from .core.logger import setup_logging
import os
from .utils.time_helper import get_trading_window
from datetime import timezone, timedelta
from typing import Dict, Any
from .models import BacktestRecord

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
    strategy_name: str = "RsiMacd"
    params: Dict[str, Any] = {}

class MarketDataRequest(BaseModel):
    start_date: str
    end_date: str
    area: str = "SE3"
    freq: str = "1h" # 1h, 1d
  
class FeatureDebugRequest(BaseModel):
    area: str = "SE3"
    start_date: str
    end_date: str

class OptimizeRequest(BaseModel):
    area: str
    start_date: str
    end_date: str
    base_params: Dict[str, Any] # 包含 max_pos, force_close 等
    rules: Dict[str, List[Dict]] # 基础策略逻辑
    param_grid: Dict[str, List[float]] # {"rsi_buy": [20, 25, 30], "rsi_sell": [70, 80]}

task_status = {}
backtest_tasks = {}
optimization_tasks = {}

@app.post("/api/backtest/optimize")
def run_optimization_async(req: OptimizeRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    启动参数矩阵扫描
    """
    task_id = str(uuid.uuid4())
    
    # 初始化状态
    optimization_tasks[task_id] = {
        "status": "running", 
        "message": "正在初始化参数矩阵...",
        "progress": 0
    }
    
    # 放入后台队列
    background_tasks.add_task(optimization_worker, task_id, req.dict())
    
    return {"status": "success", "task_id": task_id}

# 3. 新增查询状态接口
@app.get("/api/backtest/optimize/status/{task_id}")
def get_optimization_status(task_id: str):
    task = optimization_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

# 4. 实现后台 Worker
def optimization_worker(task_id: str, req_data: dict):
    # 手动创建 Session (因为离开了 Request 上下文)
    from .database import SessionLocal
    db = SessionLocal()
    try:
        base_req = {
            "area": req_data['area'],
            "start_date": req_data['start_date'],
            "end_date": req_data['end_date'],
            "rules": req_data['rules'],
            "max_pos": req_data['base_params'].get('max_pos', 5.0),
            "force_close_minutes": req_data['base_params'].get('force_close_minutes', 0),
            "enable_slippage": req_data['base_params'].get('enable_slippage', False),

            # 【新增】透传止盈止损参数
            "take_profit_pct": req_data['base_params'].get('take_profit_pct', 0.0),
            "stop_loss_pct": req_data['base_params'].get('stop_loss_pct', 0.0)
        }
        
        # 调用优化器 (注意：run_grid_search 最好能支持回调汇报进度)
        result = optimizer.run_grid_search(db, base_req, req_data['param_grid'])
        
        optimization_tasks[task_id].update({
            "status": "completed",
            "data": result, # 这里包含 results 列表
            "message": "优化完成"
        })
        
    except Exception as e:
        optimization_tasks[task_id].update({
            "status": "failed",
            "message": str(e)
        })
    finally:
        db.close()

@app.delete("/api/backtest/history/{record_id}")
def delete_backtest_history(record_id: str, db: Session = Depends(get_db)):
    record = db.query(BacktestRecord).filter(BacktestRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    
    db.delete(record)
    db.commit()
    return {"status": "success", "message": "Deleted successfully"}

@app.get("/api/backtest/reproduce/{record_id}/{contract_id}")
def reproduce_contract_result(record_id: str, contract_id: str, db: Session = Depends(get_db)):
    """
    【不可变复现接口】
    1. 交易记录：直接从历史存档读取 (Immutable)
    2. K线数据：从行情表实时读取 (Market Data)
    """
    # 1. 查历史记录
    record = db.query(BacktestRecord).filter(BacktestRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    
    # 2. 在 JSON 中找到该合约的存档
    contract_stat = next((c for c in record.contract_stats if c['cid'] == contract_id), None)
    if not contract_stat:
        raise HTTPException(status_code=404, detail="该回测记录中未找到此合约")
        
    # 3. 提取已存档的交易记录
    trades_detail = contract_stat.get('txs', [])
    
    # 4. 获取 K 线数据 (只查行情表，不跑策略)
    # 我们需要构造完整的时间轴，与 backtest.py 的逻辑保持一致 (透传所有分钟)
    
    # 4.1 解析时间窗口
    area = record.area
    # 注意：存档里的时间是字符串，需要转 datetime
    try:
        open_ts_str = contract_stat['open_t'] # "2025-12-01 13:00"
        close_ts_str = contract_stat['close_t']
        
        # 简单处理：利用 pandas 生成时间轴
        full_idx = pd.date_range(start=open_ts_str, end=close_ts_str, freq='1min', inclusive='left')
    except Exception as e:
        return {"status": "error", "msg": f"时间解析失败: {e}"}

    # 4.2 查询数据库 K 线
    from .models import MarketCandle
    candles = db.query(MarketCandle).filter(
        MarketCandle.contract_id == contract_id,
        MarketCandle.area == area
    ).all()
    
    # 转 DataFrame 方便合并
    if not candles:
        # 如果行情数据丢了，至少还能看交易记录表格
        return {
            "status": "success",
            "data": { "chart": [], "details": trades_detail, "msg": "K线数据缺失" }
        }

    df_candles = pd.DataFrame([{
        "timestamp": c.timestamp,
        "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume
    } for c in candles])
    
    # 确保 timestamp 是 datetime 类型且无时区 (假设 DB 存的是 UTC Naive)
    df_candles['timestamp'] = pd.to_datetime(df_candles['timestamp'])
    df_candles.set_index('timestamp', inplace=True)
    
    # 4.3 合并：左连接到完整时间轴
    df_merged = pd.DataFrame(index=full_idx)
    df_merged = df_merged.join(df_candles)
    
    # 5. 构造 Chart Data (适配 Lightweight Charts)
    chart_data = []
    for ts, row in df_merged.iterrows():
        ts_seconds = int(ts.timestamp())
        
        item = { "t": ts_seconds }
        
        # 只有有数据的分钟才传 OHLC，否则留白
        # 注意：这里我们直接用数据库里的 volume，不再做 ffill 检查，因为数据库里存的就是真实 K 线
        if pd.notnull(row['volume']) and row['volume'] > 0:
            item.update({
                "o": row['open'], "h": row['high'], "l": row['low'], "c": row['close'],
                "v": row['volume']
            })
            
        # 【关键】把交易标记 (Markers) 映射回 K 线图
        # 我们遍历 trades_detail，看有没有发生在这个时间点的交易
        # trades_detail 里的 time 是 "YYYY-MM-DD HH:MM"
        # 这种匹配方式效率较低 (O(N*M))，但考虑到单合约交易很少，完全没问题
        
        # 查找当前分钟是否有交易
        full_ts_str = ts.strftime('%Y-%m-%d %H:%M')
        
        # 可能会有多个动作 (比如先 Buy 后 Sell)，这里简单取最后一个非 Hold 动作
        # 或者在前端处理多个 Marker。为了简单，我们把所有动作拼接到 'a' 字段?
        # Lightweight Charts 的 setMarkers 是独立的数组，其实不需要绑在 K 线数据里返回
        # 但为了复用 renderDetailChart 的逻辑，我们还是把动作绑在 K 线上返回
        
        actions = [t for t in trades_detail if t['time'] == full_ts_str]
        if actions:
            last_action = actions[-1]
            item['a'] = last_action['action'] # BUY / SELL / FORCE_CLOSE
            # item['s'] = last_action['signal']
            
        chart_data.append(item)

    return {
        "status": "success",
        "data": {
            "chart": chart_data,
            "details": trades_detail
        }
    }

@app.get("/api/backtest/history")
def get_backtest_history(limit: int = 20, db: Session = Depends(get_db)):
    """获取最近的回测记录"""
    records = db.query(BacktestRecord)\
        .order_by(BacktestRecord.created_at.desc())\
        .limit(limit)\
        .all()
    return {"status": "success", "data": records}

@app.post("/api/market/kline")
def get_kline_data(req: MarketDataRequest, db: Session = Depends(get_db)):
    data = market_data.get_ohlcv_data(db, req.area, req.start_date, req.end_date, req.freq)
    return {"status": "success", "data": data}

@app.post("/api/backtest/run")
def run_strategy_backtest_async(req: BacktestRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    【异步回测接口】提交回测任务，立即返回 Task ID
    """
    task_id = str(uuid.uuid4())
    backtest_tasks[task_id] = {"status": "running", "progress": 0, "message": "初始化中..."}
    
    # 将任务放入后台运行
    background_tasks.add_task(backtest_worker, task_id, req.dict())
    
    return {"status": "success", "task_id": task_id}

# 3. 新增查询回测状态的接口
@app.get("/api/backtest/status/{task_id}")
def get_backtest_status(task_id: str):
    task = backtest_tasks.get(task_id)
    if not task:
        return {"status": "not_found"}
    return task

# 4. 后台工作函数 (Worker)
def backtest_worker(task_id: str, req_data: dict):
    # 手动创建 DB Session，因为 BackgroundTasks 无法复用 Depends 的 Session
    from .database import SessionLocal
    db = SessionLocal()
    try:
        backtest_tasks[task_id]["message"] = "正在加载数据..."
        
        # 调用回测服务
        # 注意：我们需要修改 services/backtest.py 让它支持进度汇报（可选），目前先直接跑
        result = backtest.run_strategy_backtest(
            db, 
            req_data['start_date'], 
            req_data['end_date'], 
            req_data['area'], 
            req_data['strategy_name'], 
            **req_data['params']
        )
        
        if result['status'] == 'success':
            backtest_tasks[task_id].update({
                "status": "completed", 
                "data": result['data'],
                "message": "回测完成"
            })
        else:
            backtest_tasks[task_id].update({
                "status": "failed", 
                "message": result.get('msg', '未知错误')
            })
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        backtest_tasks[task_id].update({
            "status": "failed", 
            "message": f"系统错误: {str(e)}"
        })
    finally:
        db.close()

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
@app.get("/api/market/candles/{area}/{contract_id}")
def get_contract_candles(area: str, contract_id: str, db: Session = Depends(get_db)):
    from .models import MarketCandle
    
    candles = db.query(MarketCandle).filter(
        MarketCandle.contract_id == contract_id,
        MarketCandle.area == area
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

@app.post("/api/debug/features")
def debug_market_features(req: FeatureDebugRequest, db: Session = Depends(get_db)):
    """
    【调试接口】查看 Feature Engine 计算出的技术指标
    """
    try:
        # 调用我们刚写的 feature_engine
        df = feature_engine.get_market_features(db, req.area, req.start_date, req.end_date)
        
        if df.empty:
            return {"status": "empty", "msg": "该时间段无数据，请检查 K 线生成任务是否已运行"}
        
        # 截取最后 20 行展示
        tail_df = df.tail(20).reset_index()
        
        # 格式化时间戳
        records = tail_df.to_dict(orient='records')
        for r in records:
            if r.get('timestamp'):
                r['timestamp'] = r['timestamp'].strftime('%Y-%m-%d %H:%M')
            
        return {
            "status": "success",
            "feature_columns": list(df.columns), # 让你确认列名是否正确
            "data_count": len(df),
            "sample_data": records
        }
    except Exception as e:
        # 打印详细堆栈以便调试
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/market/check-signal/{area}")
def check_signal_manual(area: str, db: Session = Depends(get_db)):
    """
    手动触发一次实盘信号检查
    """
    try:
        result = live_runner.run_live_analysis(db, area)
        return {"status": "success", "data": result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "detail": str(e)}