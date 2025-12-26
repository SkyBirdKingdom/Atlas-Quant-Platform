from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session
from .database import SessionLocal
from .services import fetcher, kline_generator, live_runner
from .services.live_trader import LiveTrader
import logging
from datetime import datetime, timedelta, timezone
from .models import MarketCandle
from .services.order_flow.manager import OrderFlowManager

logger = logging.getLogger("JobScheduler")

# 创建一个全局实例 (保持状态)
# 默认启动为 PAPER (模拟盘) 模式
trader_instance = LiveTrader(area="SE3", mode="PAPER")

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
        ts = last_record[0]
        # 【关键修复】如果读出来的时间没有时区信息 (Naive)，给它强行加上 UTC
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
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
            
            # --- 阶段二：实盘信号分析 (新增) ---
            try:
                # 只有当数据是“新鲜”的（比如最近1小时内有数据），才跑分析
                # 防止补录一年前数据时疯狂报警
                latest_check = get_last_kline_time(db, area)
                if latest_check > target_end_dt - timedelta(hours=2):
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

        scheduler.add_job(
            run_live_trading_job, 
            trigger=IntervalTrigger(minutes=5), 
            id="live_trading_job",
            replace_existing=True,
            max_instances=1, # 强制单实例
            misfire_grace_time=300
        )

        scheduler.add_job(
            order_flow_sync_job,
            id="startup_order_flow_sync", # ID 必须和上面的不一样
            name="Startup Order Flow Sync",
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

        scheduler.add_job(
            order_flow_sync_job,
            trigger=IntervalTrigger(hours=1), # 或 minutes=30
            id="auto_order_flow_sync",
            name="Order Flow Auto Sync",
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