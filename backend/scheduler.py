from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session
from .database import SessionLocal
from .services import fetcher, kline_generator
import logging
from datetime import datetime, timedelta, timezone
from .models import MarketCandle

logger = logging.getLogger("JobScheduler")

# 创建调度器实例
scheduler = BackgroundScheduler()

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

def get_last_kline_time(db, area):
    """
    查询数据库中该区域最新的 K 线时间戳
    """
    # 假设你的 KLine 模型叫 KLineModel，时间字段叫 timestamp
    last_record = db.query(MarketCandle.timestamp)\
                    .filter(MarketCandle.area == area)\
                    .order_by(MarketCandle.timestamp.desc())\
                    .first()
    
    if last_record:
        return last_record[0]
    else:
        # 如果数据库是空的，给一个默认的起始时间，2024-12-31 23:59:00 UTC
        return datetime(2024, 12, 31, 23, 59, 0, tzinfo=timezone.utc)

def kline_job_function():
    """
    改进后的定时任务：自动断点续传，从上次结束的地方开始生成，直到追上现在
    """
    db = SessionLocal()
    try:
        # 设定结束时间为当前时间（稍微留点余量，比如延迟1分钟，确保Trade数据已落库）
        target_end_dt = datetime.now(timezone.utc) - timedelta(minutes=1)
        
        for area in ["SE1", "SE2", "SE3", "SE4"]:
            # 1. 智能获取开始时间：从数据库里找上次最后生成的时间
            last_kline_time = get_last_kline_time(db, area)
            
            # 下一根 K 线的开始时间应该是上一根的时间 + 1分钟 (假设是1分钟K线)
            start_dt = last_kline_time + timedelta(minutes=1)
            
            # 如果开始时间已经比现在还晚，说明不需要生成，跳过
            if start_dt >= target_end_dt:
                continue

            # 2. 打印日志，方便观察追赶进度
            logger.info(f"[{area}] 检测到数据断点，开始追赶数据: {start_dt} -> {target_end_dt}")
            
            # 3. 分批次处理 (非常重要！)
            # 如果中间断了几天，直接跑几天的数据可能会内存溢出或数据库超时
            # 建议切分成小块，比如每次最多补 6 小时的数据
            chunk_size = timedelta(hours=6)
            current_pointer = start_dt
            
            while current_pointer < target_end_dt:
                # 确定当前批次的结束时间
                batch_end = min(current_pointer + chunk_size, target_end_dt)
                
                start_str = current_pointer.strftime('%Y-%m-%dT%H:%M:%SZ')
                end_str = batch_end.strftime('%Y-%m-%dT%H:%M:%SZ')
                
                # 调用生成逻辑
                kline_generator.generate_1min_candles(db, area, start_str, end_str)
                
                # 移动指针
                current_pointer = batch_end
                db.commit() # 每一批次提交一次，防止长事务

    except Exception as e:
        logger.error(f"Kline Gen Job Error: {e}")
        db.rollback()
    finally:
        db.close()

def start_scheduler():
    if not scheduler.running:
        # 添加任务：每 1 小时执行一次
        # 'replace' 表示如果任务已存在，覆盖它
        scheduler.add_job(
            job_function,
            trigger=IntervalTrigger(hours=1), 
            id="auto_sync_nordpool",
            name="NordPool Auto Sync",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True
        )

        scheduler.add_job(
            kline_job_function, 
            trigger=IntervalTrigger(minutes=15), 
            id="auto_kline_gen",
            name="Realtime Kline Gen",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True
        )

        # 不加 trigger 参数，默认就是 "DateTrigger(run_date=now)"，即立即执行一次
        scheduler.add_job(
            job_function, 
            id="startup_sync_immediate",
            name="Startup Sync",
            replace_existing=True,
            misfire_grace_time=3600
        )
        scheduler.add_job(
            kline_job_function,
            id="startup_kline_immediate",
            name="Startup Kline Immediate",
            replace_existing=True,
            misfire_grace_time=3600
        )
        
        # 启动调度器
        scheduler.start()
        logger.info("✅ 后台调度器已启动：每 1 小时自动同步数据。")
        
        # --- 启动时立即运行一次 (可选) ---
        # 这样不用等 1 小时，系统重启就马上检查更新
        # scheduler.add_job(job_function, id="startup_sync") 

def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("🛑 后台调度器已关闭")