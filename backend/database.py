from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 数据库连接 URL
# 格式: postgresql://用户名:密码@地址:端口/数据库名
# 请根据你的实际配置修改这里
SQLALCHEMY_DATABASE_URL = "postgresql://postgres:123456@127.0.0.1:5432/nordpool_db"

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# 获取数据库会话的依赖函数
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()