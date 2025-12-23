# backend/core/config.py
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # 定义配置项，Pydantic 会自动从环境变量或 .env 文件中读取
    # 变量名不区分大小写
    
    # 1. 数据库
    DATABASE_URL: str
    
    # 2. Nord Pool API (可选，设置默认值)
    NORDPOOL_USER: str = ""
    NORDPOOL_PASSWORD: str = ""
    
    # 3. App 设置
    API_V1_STR: str = "/api"
    PROJECT_NAME: str = "NordPool Risk System"
    
    class Config:
        # 指定 .env 文件路径
        # 如果 .env 在 backend 根目录下
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        case_sensitive = True

# 单例模式，全系统复用
settings = Settings()