# backend/services/order_flow/fetcher.py
import requests
import logging
from datetime import datetime, timedelta
from typing import Dict, Iterator
from ...core.config import settings

logger = logging.getLogger("OrderFlowFetcher")

class OrderFlowFetcher:
    """
    Nord Pool 订单流数据获取器
    集成 STS 自动鉴权与长时段自动切片 (Generator模式)
    """
    
    def __init__(self):
        self.base_url = getattr(settings, "NORDPOOL_API_URL", "https://api.nordpoolgroup.com")
        self.username = settings.NORDPOOL_USER
        self.password = settings.NORDPOOL_PASSWORD
        self.token = None
        self.token_url = "https://sts.nordpoolgroup.com/connect/token"
        
        # 初始化时获取 Token
        self._refresh_token()

    def _refresh_token(self):
        if not self.username or not self.password:
            logger.warning("未配置 Nord Pool 账号密码，Fetcher 将无法工作")
            return

        try:
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": "Basic Y2xpZW50X21hcmtldGRhdGFfYXBpOmNsaWVudF9tYXJrZXRkYXRhX2FwaQ=="
            }
            data = {
                "grant_type": "password", 
                "scope": "marketdata_api", 
                "username": self.username, 
                "password": self.password
            }
            
            logger.info("正在刷新 Nord Pool Token...")
            resp = requests.post(self.token_url, headers=headers, data=data, timeout=10)
            resp.raise_for_status()
            self.token = resp.json().get("access_token")
            
        except Exception as e:
            logger.error(f"❌ Token 获取失败: {e}")

    def _get_headers(self) -> Dict[str, str]:
        if not self.token:
            self._refresh_token()
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _request(self, method: str, endpoint: str, params: Dict = None) -> Dict:
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        try:
            resp = requests.request(
                method, 
                url, 
                headers=self._get_headers(), 
                params=params, 
                timeout=45 
            )
            
            if resp.status_code == 401:
                logger.warning("Token 过期 (401)，刷新重试...")
                self._refresh_token()
                resp = requests.request(
                    method, 
                    url, 
                    headers=self._get_headers(), 
                    params=params, 
                    timeout=45
                )

            resp.raise_for_status()
            return resp.json()
            
        except Exception as e:
            logger.error(f"API 请求失败: {url} | {e}")
            raise

    def fetch_recent_orders(self, area: str, start_time: datetime, end_time: datetime) -> Iterator[Dict]:
        """
        【接口 B (流式增强版)】获取近期订单更新
        自动处理 4 小时限制，使用 Generator 逐个返回切片数据
        避免一次性加载大量数据导致内存溢出
        """
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        endpoint = "/api/v2/Intraday/OrderRevisions/ByUpdatedTime"

        # 按 4小时 切片
        chunk_size = timedelta(hours=4)
        current_start = start_time
        
        count = 0
        while current_start < end_time:
            current_end = min(current_start + chunk_size, end_time)
            logger.info(f"开始流式获取订单流: {area} [{current_start} -> {current_end}]")
            
            params = {
                "area": area,
                "updatedTimeFrom": current_start.strftime(fmt),
                "updatedTimeTo": current_end.strftime(fmt)
            }
            
            try:
                # logger.debug(f"  -> 请求片段: {params['updatedTimeFrom']} ~ {params['updatedTimeTo']}")
                data = self._request("GET", endpoint, params)
                count += 1
                
                # 【关键修改】直接 yield 当前切片数据，而不是合并
                yield data
                    
            except Exception as e:
                logger.error(f"片段获取失败 [{current_start}]: {e}")
                # 即使一段失败，也尝试继续获取下一段
                
            current_start = current_end
            
        logger.info(f"✅ 流式获取结束，共处理 {count} 个片段")

    def fetch_historical_revisions(self, area: str, contract_id: str, delivery_date: str) -> Dict:
        """
        【接口 A】获取历史订单簿变更 (Revisions)
        对应文档: Returns all intraday order book changes...
        数据延迟: 23小时 (T+1)
        
        :param delivery_date: 格式 "YYYY-MM-DD"
        """
        endpoint = "/api/v2/Intraday/OrderBook/ByContractId" 
        
        params = {
            "area": area,
            "contractId": contract_id,
            "deliveryDateUtc": delivery_date
        }
        
        logger.info(f"正在获取历史盘口快照: {contract_id} ({delivery_date})")
        return self._request("GET", endpoint, params)


    def fetch_contract_list(self, area: str, delivery_date: str) -> Dict:
            """
            【辅助接口】获取某日的合约列表
            对应文档: Returns a list of contracts with available order books...
            用于先拿到 contractId，再调用 fetch_historical_revisions
            """
            endpoint = "/api/v2/Intraday/OrderBook/ContractsIds/ByArea"
            
            params = {
                "area": area,
                "deliveryDateUtc": delivery_date
            }
            
            return self._request("GET", endpoint, params)
