# backend/core/config.py
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # 1. 数据库 (必须)
    DATABASE_URL: str
    
    # 2. Nord Pool API (可选)
    NORDPOOL_USER: str = ""
    NORDPOOL_PASSWORD: str = ""
    NORDPOOL_API_URL: str = "https://api.nordpoolgroup.com"
    
    # 3. App 设置
    API_V1_STR: str = "/api"
    PROJECT_NAME: str = "NordPool Risk System"
    
    # 4. Server 设置 (新增，解决报错)
    # Pydantic 会自动把 .env 里的字符串 "8000" 转成 int
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    DEBUG: bool = True
    
    class Config:
        # 指定 .env 文件路径
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        case_sensitive = True
        # 【关键】设置为 ignore，这样如果 .env 里还有其他多余字段，也不会报错，而是自动忽略
        extra = "ignore"

# 单例模式，全系统复用
settings = Settings()