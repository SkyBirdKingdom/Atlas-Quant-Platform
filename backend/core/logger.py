import logging
import sys
import os
from logging.handlers import TimedRotatingFileHandler

# 日志格式
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def setup_logging():
    """
    配置全局日志系统
    """
    # 1. 确保 logs 目录存在
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 2. 获取 Root Logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # 清空已有的 Handlers (防止重复打印)
    if logger.hasHandlers():
        logger.handlers.clear()

    # 3. Handler 1: 控制台输出 (StreamHandler)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(console_handler)

    # 4. Handler 2: 文件输出 (按天切割)
    # 每天午夜 (midnight) 切割一次，最多保留 30 天
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "app.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    logger.addHandler(file_handler)

    # 5. 调整第三方库的日志级别 (防止太吵)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING) # 屏蔽普通 HTTP 请求日志，除非报错
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    
    # 6. 打印一条测试日志
    logging.getLogger("System").info(f"✅ 日志系统初始化完成。日志文件路径: {log_dir}")