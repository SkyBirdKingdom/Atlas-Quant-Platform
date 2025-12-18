import axios from 'axios';

// 创建 axios 实例，指向你的 FastAPI 地址
const api = axios.create({
  baseURL: 'http://127.0.0.1:8000/api', // 你的后端地址
  timeout: 300000 // 300秒超时，因为抓取数据可能很慢
});

// 1. 触发后台抓取任务
export const triggerFetch = (data) => {
  return api.post('/admin/fetch-data', data);
};

// 2. 获取分析数据
export const getAnalysis = (params) => {
  return api.post('/analyze', params);
};

// 检查数据存在性
export const getDataCalendar = (area) => api.get(`/data-availability?area=${area}`);

// 区间分析
export const getRangeAnalysis = (params) => api.post('/analyze/range', params);

// 检查任务状态
export const getTaskStatus = (taskId) => api.get(`/tasks/${taskId}`);

export const getSystemStatus = () => api.get('/system/status');

export const runBacktest = (data) => api.post('/backtest/run', data);

export const getAppLogs = () => api.get('/admin/logs?lines=50');

// 获取某天的合约列表
export const getContracts = (date, area) => 
  api.get(`/market/contracts?date=${date}&area=${area}`);

// 获取 K 线数据
export const getCandles = (area, contractId) => 
  api.get(`/market/candles/${area}/${contractId}`);

export const getTradesbyContract = (area, contractId) => 
  api.get(`/debug/trades/${area}/${contractId}`);

export const getBacktestStatus = (taskId) => api.get(`/backtest/status/${taskId}`);

export const getBacktestHistory = () => api.get('/backtest/history');

export const deleteBacktestHistory = (recordId) => api.delete(`/backtest/history/${recordId}`);

export const reproduceContract = (recordId, contractId) => 
  api.get(`/backtest/reproduce/${recordId}/${contractId}`);

export const getBacktestOptimize = (data) => api.post('/backtest/optimize', data);

export const getOptimizationStatus = (taskId) => api.get(`/backtest/optimize/status/${taskId}`);

export default api;