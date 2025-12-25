# scripts/test_order_flow.py
import sys
import os
from datetime import datetime, timedelta, timezone
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import SessionLocal, init_db
from backend.services.order_flow import OrderFlowFetcher, OrderFlowProcessor, OrderFlowService
from backend.core.logger import setup_logging

setup_logging()
logger = logging.getLogger("TestScript")

def main():
    logger.info("🚀 开始订单流数据集成测试 (流式版)...")
    
    init_db() 
    db = SessionLocal()
    
    try:
        fetcher = OrderFlowFetcher()
        processor = OrderFlowProcessor()
        storage = OrderFlowService(db)
        
        area = "SE3"
        # 故意设置一个较长的时间段来测试切片 (例如过去5小时，触发切片)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=5)
        
        logger.info(f"测试范围: {area} | {start_time.isoformat()} -> {end_time.isoformat()}")
        
        total_ticks = 0
        
        # 【关键修改】使用 for 循环消费生成器
        # 每次循环处理一个 4小时切片的数据
        for chunk_idx, raw_data in enumerate(fetcher.fetch_recent_orders(area, start_time, end_time)):
            
            # 1. 立即处理
            ticks = processor.process_recent_orders_response(raw_data)
            logger.info(f"📦 片段 {chunk_idx+1}: 解析出 {len(ticks)} 条数据")
            
            if ticks:
                # 2. 立即入库 (处理完即释放内存)
                storage.save_ticks(ticks)
                total_ticks += len(ticks)
                
                # 打印预览
                if chunk_idx == 0:
                    logger.info("--- 数据预览 (First Chunk Top 3) ---")
                    for t in ticks[:3]:
                        logger.info(f"[{t.type}] {t.timestamp} | P:{t.price} | V:{t.volume} | {t.aggressor_side}")

        logger.info(f"💾 全部完成！共入库 {total_ticks} 条 Tick 数据")

    except Exception as e:
        logger.error(f"❌ 测试过程中发生错误: {e}", exc_info=True)
    finally:
        db.close()

def export_order_flow_ticks_to_csv(area: str, output_file: str):
    """
    辅助函数：将指定区域的 Order Flow Tick 数据导出为 CSV 文件，便于离线分析
    """
    import pandas as pd
    db = SessionLocal()
    try:
        from backend.models import OrderFlowTick
        query = db.query(OrderFlowTick).filter(OrderFlowTick.delivery_area == area)
        df = pd.read_sql(query.statement, db.bind)
        df.to_csv(output_file, index=False)
        logger.info(f"✅ 已导出 {len(df)} 条 Order Flow Tick 到文件: {output_file}")
    finally:
        db.close()

if __name__ == "__main__":
    export_order_flow_ticks_to_csv("SE3", "order_flow_ticks_se3_20251223.csv")
    # main()