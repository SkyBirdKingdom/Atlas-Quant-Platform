from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session
from sqlalchemy import text
from .database import SessionLocal
from .services import fetcher, kline_generator, live_runner
from .services.live_trader import LiveTrader
import logging
from datetime import datetime, timedelta, timezone
from .models import MarketCandle, FetchState, KlineGenState
from .services.order_flow.manager import OrderFlowManager

logger = logging.getLogger("JobScheduler")

# 创建一个全局实例 (保持状态)
# 默认启动为 PAPER (模拟盘) 模式
trader_instance = LiveTrader(area="SE3", mode="PAPER")

# 创建调度器实例
scheduler = BackgroundScheduler(timezone=timezone.utc)

def job_function():
    """
    包装器：创建 DB 会话 -> 执行同步 -> 关闭会话
    """
    db = SessionLocal()
    try:
        fetcher.sync_all_areas(db)
    except Exception as e:
        logger.error(f"Job Execution Error: {e}")
    finally:
        db.close()

def get_kline_progress(db, area):
    """
    【修改】优先从状态表读取进度，如果没有则回退到查数据表
    """
    # 1. 查状态表 (推荐)
    state = db.query(KlineGenState).filter(KlineGenState.area == area).first()
    if state and state.last_generated_time:
        ts = state.last_generated_time
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        return ts
        
    # 2. 回退：查 K 线表最大时间 (兼容旧数据)
    last_record = db.query(MarketCandle.timestamp)\
                    .filter(MarketCandle.area == area)\
                    .order_by(MarketCandle.timestamp.desc())\
                    .first()
    if last_record:
        ts = last_record[0]
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        return ts
    
    # 3. 默认起始点
    return datetime(2024, 12, 31, 23, 59, 0, tzinfo=timezone.utc)

def update_kline_progress(db, area, timestamp):
    """
    【新增】更新生成进度
    """
    state = db.query(KlineGenState).filter(KlineGenState.area == area).first()
    if not state:
        state = KlineGenState(area=area, last_generated_time=timestamp)
        db.add(state)
    else:
        state.last_generated_time = timestamp
        state.updated_at = datetime.now(timezone.utc)
    # 注意：这里不commit，由外层commit

def _get_fetch_progress(db, area):
    state = db.query(FetchState).filter(FetchState.area == area).first()
    if state and state.last_fetched_time:
        ts = state.last_fetched_time
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        return ts
    return None

def kline_job_function():
    """
    改进后的定时任务：自动断点续传，从上次结束的地方开始生成，直到追上现在
    """
    db = SessionLocal()
    try:
        now_dt = datetime.now(timezone.utc)
        
        for area in ["SE1", "SE2", "SE3", "SE4"]:
            # 1. 获取上次生成的截止时间
            last_progress = get_kline_progress(db, area)
            start_dt = last_progress + timedelta(minutes=1)
            
            # 2. 检查数据源同步进度 (红绿灯)
            fetch_limit = _get_fetch_progress(db, area)
            
            if not fetch_limit:
                logger.info(f"[{area}] 等待 Trade 数据同步...")
                continue
                
            # 目标：不能超过数据源的进度
            safe_end_dt = min(now_dt, fetch_limit)
            
            if start_dt >= safe_end_dt:
                continue

            logger.info(f"[{area}] K线生成: {start_dt} -> {safe_end_dt}")
            
            chunk_size = timedelta(hours=6)
            current_pointer = start_dt
            
            while current_pointer < safe_end_dt:
                batch_end = min(current_pointer + chunk_size, safe_end_dt)
                
                start_str = current_pointer.strftime('%Y-%m-%dT%H:%M:%SZ')
                end_str = batch_end.strftime('%Y-%m-%dT%H:%M:%SZ')

                # 3. 调用生成器 (纯 SQL 操作)
                # 无论这一段有没有数据，生成器都会正确处理(有则插，无则跳过)
                generated_count = kline_generator.generate_1min_candles(db, area, start_str, end_str)
                
                # 4. 【关键】显式更新进度指针
                # 无论 generated_count 是 0 还是 100，我们都认为这段时间 "已处理"
                # 这彻底消除了对 "Gap Candle" 的需求
                update_kline_progress(db, area, batch_end)
                
                if generated_count > 0:
                    logger.info(f"[{area}] {start_str} -> {end_str}: 生成 {generated_count} 条 K线")

                current_pointer = batch_end
                db.commit()
            
            # --- 阶段二：实盘信号分析 (新增) ---
            try:
                # 只有当数据是“新鲜”的（比如最近1小时内有数据），才跑分析
                # 防止补录一年前数据时疯狂报警
                latest_check = get_kline_progress(db, area)
                if latest_check > now_dt - timedelta(hours=2):
                    result = live_runner.run_live_analysis(db, area)
                    
                    if result and result['signal'] != "NEUTRAL":
                        logger.info(f"🚀🚀🚀 [{area}] 触发重磅信号: {result['signal']} | RSI: {result['rsi']:.2f}")
                        # TODO: 这里是未来接 Telegram 报警的地方
            except Exception as e:
                logger.error(f"[{area}] 信号分析失败: {e}")

    except Exception as e:
        logger.error(f"Kline Gen Job Error: {e}")
        db.rollback()
    finally:
        db.close()

def run_live_trading_job():
    """
    实盘/模拟盘 调度任务
    建议每 15 分钟执行一次
    """
    logger.info("⏰ 触发实盘循环任务...")
    trader_instance.run_tick()

def order_flow_sync_job():
    """
    【新增】订单流自动同步任务
    """
    db = SessionLocal()
    try:
        manager = OrderFlowManager(db)
        manager.sync_all()
    except Exception as e:
        logger.error(f"Order Flow Sync Job Error: {e}")
    finally:
        db.close()

def start_scheduler():
    if not scheduler.running:

        now = datetime.now(timezone.utc)
        # 添加任务：每 1 小时执行一次
        # 'replace' 表示如果任务已存在，覆盖它
        scheduler.add_job(
            job_function,
            trigger=IntervalTrigger(hours=1, timezone=timezone.utc), 
            id="auto_sync_nordpool",
            name="NordPool Auto Sync",
            replace_existing=True,
            misfire_grace_time=3600,
            max_instances=1,
            next_run_time=now
        )

        scheduler.add_job(
            kline_job_function, 
            trigger=IntervalTrigger(minutes=15, timezone=timezone.utc), 
            id="auto_kline_gen",
            name="Realtime Kline Gen",
            replace_existing=True,
            misfire_grace_time=3600,
            max_instances=1,
            next_run_time=now + timedelta(minutes=1),
        )

        scheduler.add_job(
            order_flow_sync_job,
            trigger=IntervalTrigger(hours=1, timezone=timezone.utc),
            id="startup_order_flow_sync", # ID 必须和上面的不一样
            name="Startup Order Flow Sync",
            replace_existing=True,
            misfire_grace_time=3600,
            max_instances=1,
            next_run_time=now
        )

        scheduler.add_job(
            run_live_trading_job, 
            trigger=IntervalTrigger(minutes=5, timezone=timezone.utc), 
            id="live_trading_job",
            name="Live Trading Heartbeat",
            replace_existing=True,
            max_instances=1, # 强制单实例
            misfire_grace_time=300,
            next_run_time=now
        )

        # 启动调度器
        scheduler.start()
        logger.info("✅ 后台调度器已启动 (UTC Mode)")
        
def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("🛑 后台调度器已关闭")