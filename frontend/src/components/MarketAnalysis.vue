<template>
  <div class="market-container">
    <div class="sidebar">
      <div class="sidebar-header">
        <div class="header-row">
          <el-icon><Histogram /></el-icon>
          <h3>市场微观分析</h3>
        </div>
      </div>
      
      <div class="filter-box">
        <el-form label-position="top" size="small">
          <el-form-item label="交易区域">
            <el-radio-group v-model="selectedArea" @change="loadContracts" style="width: 100%">
              <el-radio-button label="SE1" />
              <el-radio-button label="SE2" />
              <el-radio-button label="SE3" />
              <el-radio-button label="SE4" />
            </el-radio-group>
          </el-form-item>
          
          <el-form-item label="交割日期">
            <el-date-picker
              v-model="selectedDate"
              type="date"
              value-format="YYYY-MM-DD"
              :clearable="false"
              @change="loadContracts"
              style="width: 100%"
            />
          </el-form-item>
        </el-form>
      </div>

      <div class="contract-list" v-loading="listLoading">
        <div 
          v-for="c in contracts" 
          :key="c.contract_id"
          class="contract-item"
          :class="{ active: currentContractId === c.contract_id }"
          @click="selectContract(c)"
        >
          <div class="c-info">
            <span class="c-time">{{ c.contract_name }}</span>
            <span class="c-id">{{ c.label }}</span>
          </div>
          <el-tag size="small" :type="c.type === 'PH' ? '' : 'warning'" effect="plain">{{ c.type }}</el-tag>
        </div>
        <div v-if="contracts.length === 0" class="empty-tip">暂无合约数据</div>
      </div>
    </div>

    <div class="chart-area">
      <div class="chart-header" v-if="currentContract">
        <div class="title-group">
          <h2>{{ currentContract.label }}</h2>
          <span class="sub-title">{{ currentContract.contract_id }}</span>
        </div>
        
        <div class="right-group">
          <el-button-group class="analysis-btns">
            <el-button type="warning" plain icon="Search" @click="onAnalyzeFull" :loading="forensicLoading">
              全时段分析
            </el-button>
            <el-button type="danger" plain icon="Aim" @click="onAnalyzeVisible" :loading="forensicLoading">
              分析当前视野
            </el-button>
          </el-button-group>
          
          <el-divider direction="vertical" />

          <el-button type="primary" link icon="View" @click="openDebugDialog">
            Trade明细
          </el-button>
          
          <div class="indicators">
            <el-tag type="info" effect="dark">C: {{ lastPrice.close }}</el-tag>
            <el-tag color="#fff6e0" style="color: #ff9800; border: 1px solid #ffe0b2">VWAP: {{ lastPrice.vwap }}</el-tag>
          </div>
        </div>
      </div>
      
      <div ref="chartContainer" class="tv-chart"></div>
      <div ref="toolTip" class="floating-tooltip"></div>
      
      <div v-if="!currentContract" class="chart-placeholder">
        <el-empty description="请从左侧选择一个合约查看 K 线走势" />
      </div>
    </div>

    <el-dialog v-model="debugVisible" title="原始交易数据 (Raw Trades)" width="800px">
      <el-table :data="debugData" height="500" stripe style="width: 100%">
        <el-table-column prop="trade_id" label="Trade ID" width="120" />
        <el-table-column prop="time_utc" label="时间 (UTC)" sortable width="180" />
        <el-table-column prop="price" label="价格" sortable />
        <el-table-column prop="volume" label="量" sortable />
        <el-table-column prop="area" label="区域" width="80" />
      </el-table>
    </el-dialog>

    <el-dialog v-model="forensicVisible" title="🛡️ 市场微观取证报告 (Forensic Report)" width="900px" custom-class="forensic-dialog">
      <div v-if="forensicResult.length === 0" class="empty-report">
        <el-result icon="success" title="未发现显著异常" sub-title="在该时间段内，价格波动与订单流行为均在正常阈值范围内。"></el-result>
      </div>
      
      <div v-else class="report-list">
        <el-collapse v-model="activeNames">
          <el-collapse-item v-for="(item, index) in forensicResult" :key="index" :name="index">
            <template #title>
              <div class="report-title">
                <el-tag :type="item.type === 'Pump' ? 'danger' : 'success'" effect="dark" style="margin-right: 10px">
                  {{ item.type === 'Pump' ? '拉升 (Pump)' : '砸盘 (Dump)' }}
                </el-tag>
                <span class="time-range">{{ formatTimeStr(item.start_time) }} - {{ formatTimeStr(item.end_time) }}</span>
                <span class="change-tag">波动幅度: <b>{{ item.change_pct }}%</b></span>
              </div>
            </template>
            
            <div class="report-content">
              <div class="conclusion-box" :class="{ 'risk-high': item.microstructure_analysis.conclusion.includes('🚨') }">
                <h4>🧠 智能诊断结论</h4>
                <p>{{ item.microstructure_analysis.conclusion }}</p>
              </div>

              <div class="metrics-grid">
                <div class="metric-card">
                  <div class="label">主动买入占比 (Aggressive Buy)</div>
                  <div class="value" :class="getScoreColor(item.microstructure_analysis.aggressive_buy_ratio)">
                    {{ item.microstructure_analysis.aggressive_buy_ratio }}%
                  </div>
                  <div class="desc">若 > 80%，表明主力在扫货</div>
                </div>
                <div class="metric-card">
                  <div class="label">买单虚假撤单率 (Spoofing Buy)</div>
                  <div class="value" :class="getScoreColor(item.microstructure_analysis.spoofing_ratio_buy)">
                    {{ item.microstructure_analysis.spoofing_ratio_buy }}%
                  </div>
                  <div class="desc">若 > 80%，表明存在诱多挂单</div>
                </div>
                <div class="metric-card">
                  <div class="label">卖单虚假撤单率 (Spoofing Sell)</div>
                  <div class="value" :class="getScoreColor(item.microstructure_analysis.spoofing_ratio_sell)">
                    {{ item.microstructure_analysis.spoofing_ratio_sell }}%
                  </div>
                  <div class="desc">若 > 80%，表明存在诱空压盘</div>
                </div>
              </div>

              <div class="large-orders" v-if="item.microstructure_analysis.large_orders.length > 0">
                <h4>🐳 鲸鱼订单追踪 (>20MW)</h4>
                <el-table :data="item.microstructure_analysis.large_orders" size="small" border style="width: 100%">
                  <el-table-column prop="time" label="时间" width="120" />
                  <el-table-column prop="action" label="动作">
                     <template #default="scope">
                        <el-tag size="small" :type="scope.row.action === 'PLACED' ? '' : 'info'">{{ scope.row.action }}</el-tag>
                     </template>
                  </el-table-column>
                  <el-table-column prop="side" label="方向" width="80">
                     <template #default="scope">
                        <span :style="{color: scope.row.side === 'BUY' ? '#ef5350' : '#26a69a', fontWeight: 'bold'}">{{ scope.row.side }}</span>
                     </template>
                  </el-table-column>
                  <el-table-column prop="price" label="价格" />
                  <el-table-column prop="volume" label="量 (MW)" />
                </el-table>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, markRaw } from 'vue';
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, createSeriesMarkers } from 'lightweight-charts';
import { getContracts, getCandles, getTradesbyContract, detectForensic } from '../api/service'; // 确保 service.js 已更新
import { ElMessage } from 'element-plus';
import { Histogram, View, Search, Aim } from '@element-plus/icons-vue';

// --- 状态 ---
const selectedArea = ref('SE3');
const selectedDate = ref('2025-12-01');
const contracts = ref([]);
const listLoading = ref(false);

const currentContractId = ref(null);
const currentContract = ref(null);
const lastPrice = ref({ open: '-', high: '-', low: '-', close: '-', vwap: '-' });

const toolTip = ref(null);

// Debug
const debugVisible = ref(false);
const debugData = ref([]);

// Forensic Analysis
const forensicVisible = ref(false);
const forensicLoading = ref(false);
const forensicResult = ref([]);
const activeNames = ref([0]); // 默认展开第一个

// Charts
const chartContainer = ref(null);
let chart = null;
let candleSeries = null;
let volumeSeries = null;
let vwapSeries = null;

// --- 工具函数 ---
const formatTimeUtc = (time) => {
  const date = new Date(time * 1000);
  const hours = date.getUTCHours().toString().padStart(2, '0');
  const minutes = date.getUTCMinutes().toString().padStart(2, '0');
  return `${hours}:${minutes}`;
};

const formatDateUtc = (time) => {
  const date = new Date(time * 1000);
  const month = (date.getUTCMonth() + 1).toString().padStart(2, '0');
  const day = date.getUTCDate().toString().padStart(2, '0');
  return `${month}-${day}`;
};

// 将 Unix timestamp 转为 YYYY-MM-DD HH:mm:ss 字符串 (UTC)
const tsToString = (ts) => {
  const d = new Date(ts * 1000);
  return d.toISOString().replace('T', ' ').split('.')[0];
};

const formatTimeStr = (isoStr) => {
  if (!isoStr) return '';
  // 简单显示时间部分
  return isoStr.split('T')[1] || isoStr.split(' ')[1];
};

const getScoreColor = (score) => {
  if (score > 80) return 'text-red';
  if (score > 50) return 'text-orange';
  return 'text-green';
};

// --- 加载数据 ---
const loadContracts = async () => {
  listLoading.value = true;
  contracts.value = [];
  try {
    const res = await getContracts(selectedDate.value, selectedArea.value);
    contracts.value = res.data;
  } catch (e) {
    ElMessage.error("合约列表加载失败");
  } finally {
    listLoading.value = false;
  }
};

const openDebugDialog = async () => {
  if (!currentContractId.value) return;
  debugVisible.value = true;
  debugData.value = [];
  try {
    const res = await getTradesbyContract(selectedArea.value, currentContractId.value);
    debugData.value = res.data;
  } catch (e) {
    ElMessage.error("获取原始数据失败");
  }
};

// --- 核心：取证分析逻辑 ---
const runAnalysis = async (startTimeStr, endTimeStr) => {
  forensicLoading.value = true;
  try {
    const payload = {
      area: selectedArea.value,
      start_date: startTimeStr,
      end_date: endTimeStr,
      threshold: 0.01, // 设置 1% 的敏感度，保证能抓到波动
      contract_id: currentContractId.value
    };
    
    console.log("Analyze payload:", payload);
    const res = await detectForensic(payload);
    
    forensicResult.value = res.data.data; // 注意 API 返回结构
    forensicVisible.value = true;
    
    if (res.data.data.length > 0) {
      ElMessage.success(`分析完成，发现 ${res.data.data.length} 个异常波动段`);
    } else {
      ElMessage.info("分析完成，该时段内未发现异常");
    }
  } catch (e) {
    console.error(e);
    ElMessage.error("分析请求失败: " + (e.response?.data?.detail || e.message));
  } finally {
    forensicLoading.value = false;
  }
};

// 1. 全时段分析
const onAnalyzeFull = () => {
  if (!currentContract.value) return;
  // 使用合约定义的开盘收盘时间
  const start = tsToString(currentContract.value.open_ts);
  const end = tsToString(currentContract.value.close_ts);
  runAnalysis(start, end);
};

// 2. 视野范围分析 (实现"框选"效果)
const onAnalyzeVisible = () => {
  if (!chart) return;
  // 获取当前可见的逻辑范围
  const logicalRange = chart.timeScale().getVisibleLogicalRange();
  if (!logicalRange) return;
  
  // 将逻辑范围转换为时间范围
  // 注意：lightweight-charts 的 convertLogicalToTime 返回的是 { year, month, day } 对象或 timestamp
  // 但更简单的方法是直接 getVisibleRange (如果开启了 time scale)
  const range = chart.timeScale().getVisibleRange();
  
  if (range) {
    const start = tsToString(range.from);
    const end = tsToString(range.to);
    runAnalysis(start, end);
  } else {
    ElMessage.warning("无法获取当前视野范围，请先缩放图表");
  }
};

const selectContract = async (c) => {
  currentContractId.value = c.contract_id;
  currentContract.value = c;
  try {
    const res = await getCandles(selectedArea.value, c.contract_id);
    const rawData = res.data;
    await nextTick();
    resetChart();
    renderChart(rawData, c);
  } catch (e) {
    ElMessage.error("K线加载失败");
  }
};

const resetChart = () => {
  if (chart) {
    chart.remove();
    chart = null;
    candleSeries = null;
    volumeSeries = null;
    vwapSeries = null;
  }
};

const renderChart = (data, contractInfo) => {
  initChart(); 
  if (!chart || !candleSeries) return;

  const processedData = [...data];
  const openTs = contractInfo.open_ts;
  const closeTs = contractInfo.close_ts;

  // 数据补全逻辑 (保持原样)
  if (processedData.length > 0) {
    if (processedData[0].time > openTs) {
        processedData.unshift({ time: openTs, open: processedData[0].open, high: processedData[0].open, low: processedData[0].open, close: processedData[0].open, volume: 0, vwap: processedData[0].vwap });
    }
    if (processedData[processedData.length-1].time < closeTs) {
        processedData.push({ time: closeTs, open: processedData[processedData.length-1].close, high: processedData[processedData.length-1].close, low: processedData[processedData.length-1].close, close: processedData[processedData.length-1].close, volume: 0, vwap: processedData[processedData.length-1].vwap });
    }
  } else {
     processedData.push({ time: openTs, open: 0, high: 0, low: 0, close: 0, volume: 0, vwap: 0 });
     processedData.push({ time: closeTs, open: 0, high: 0, low: 0, close: 0, volume: 0, vwap: 0 });
  }
  
  const candleData = processedData.map(d => ({ time: d.time, open: d.open, high: d.high, low: d.low, close: d.close }));
  const volumeData = processedData.map(d => ({ time: d.time, value: d.volume, color: d.close >= d.open ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)' }));
  const vwapData = processedData.map(d => ({ time: d.time, value: d.vwap > 0 ? d.vwap : undefined }));

  candleSeries.setData(candleData);
  volumeSeries.setData(volumeData);
  vwapSeries.setData(vwapData);
  
  const markers = [];
  markers.push({ time: openTs, position: 'aboveBar', color: '#2196F3', shape: 'arrowDown', text: 'OPEN' });
  markers.push({ time: closeTs, position: 'aboveBar', color: '#E91E63', shape: 'arrowDown', text: 'CLOSE' });
  createSeriesMarkers(candleSeries, markers);
  chart.timeScale().fitContent();
  
  if (data.length > 0) {
    const lastReal = data[data.length - 1];
    lastPrice.value = {
      open: lastReal.open.toFixed(2),
      high: lastReal.high.toFixed(2),
      low: lastReal.low.toFixed(2),
      close: lastReal.close.toFixed(2),
      vwap: lastReal.vwap.toFixed(2)
    };
  }
};

const initChart = () => {
  if (!chartContainer.value) return;
  const chartInstance = createChart(chartContainer.value, {
    width: chartContainer.value.clientWidth,
    height: chartContainer.value.clientHeight,
    layout: { background: { color: '#ffffff' }, textColor: '#333' },
    grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
    rightPriceScale: { visible: true },
    timeScale: {
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (time, tickMarkType) => {
        if (tickMarkType === 2) return formatDateUtc(time);
        return formatTimeUtc(time);
      },
    }
  });
  chart = markRaw(chartInstance);
  candleSeries = markRaw(chart.addSeries(CandlestickSeries, { upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350' }));
  vwapSeries = markRaw(chart.addSeries(LineSeries, { color: '#ff9800', lineWidth: 2, title: 'VWAP', crosshairMarkerVisible: false }));
  volumeSeries = markRaw(chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' }));
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  
  chart.subscribeCrosshairMove(param => {
    if (!toolTip.value) return;
    if (param.point === undefined || !param.time || param.point.x < 0 || param.point.x > chartContainer.value.clientWidth || param.point.y < 0 || param.point.y > chartContainer.value.clientHeight) {
      toolTip.value.style.display = 'none';
      return;
    }
    toolTip.value.style.display = 'block';
    const candleData = param.seriesData.get(candleSeries);
    const vwapData = param.seriesData.get(vwapSeries);
    const volumeData = param.seriesData.get(volumeSeries);
    if (!candleData) { toolTip.value.style.display = 'none'; return; }

    const priceHtml = `<div style="color: #333; font-weight: bold; margin-bottom: 4px">${formatTimeUtc(param.time)} (UTC)</div>
      <div style="display: flex; justify-content: space-between;"><span>O:</span> <span>${candleData.open.toFixed(2)}</span></div>
      <div style="display: flex; justify-content: space-between;"><span>H:</span> <span>${candleData.high.toFixed(2)}</span></div>
      <div style="display: flex; justify-content: space-between;"><span>L:</span> <span>${candleData.low.toFixed(2)}</span></div>
      <div style="display: flex; justify-content: space-between;"><span>C:</span> <span>${candleData.close.toFixed(2)}</span></div>`;
    const vwapVal = vwapData && vwapData.value ? vwapData.value.toFixed(2) : '-';
    const vwapHtml = `<div style="display: flex; justify-content: space-between; color: #ff9800"><span>VWAP:</span> <span>${vwapVal}</span></div>`;
    const volVal = volumeData && volumeData.value ? volumeData.value.toFixed(1) : '0';
    const volHtml = `<div style="display: flex; justify-content: space-between; color: #26a69a"><span>Vol:</span> <span>${volVal}</span></div>`;
    toolTip.value.innerHTML = priceHtml + vwapHtml + volHtml;
    
    let left = param.point.x + 15;
    let top = param.point.y + 15;
    if (left + 160 > chartContainer.value.clientWidth) left = param.point.x - 175;
    if (top + 150 > chartContainer.value.clientHeight) top = param.point.y - 160;
    toolTip.value.style.left = left + 'px';
    toolTip.value.style.top = top + 'px';
  });
};

const handleResize = () => {
  if (chart && chartContainer.value) chart.applyOptions({ width: chartContainer.value.clientWidth, height: chartContainer.value.clientHeight });
};

onMounted(() => {
  loadContracts();
  window.addEventListener('resize', handleResize);
});

onUnmounted(() => {
  resetChart();
  window.removeEventListener('resize', handleResize);
});
</script>

<style scoped>
.market-container { display: flex; height: 800px; background: #fff; border: 1px solid #e0e0e0; border-radius: 4px; }
.sidebar { width: 300px; border-right: 1px solid #eee; display: flex; flex-direction: column; }
.sidebar-header { padding: 15px; background: #f8f9fa; border-bottom: 1px solid #eee; }
.header-row { display: flex; align-items: center; gap: 8px; color: #333; }
.header-row h3 { margin: 0; font-size: 16px; }
.filter-box { padding: 15px; border-bottom: 1px solid #eee; }
.contract-list { flex: 1; overflow-y: auto; }
.contract-item { padding: 12px 20px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f5f5f5; transition: background 0.2s; }
.contract-item:hover { background: #f5f7fa; }
.contract-item.active { background: #ecf5ff; border-left: 4px solid #409eff; }
.c-info { display: flex; flex-direction: column; }
.c-time { font-weight: 600; font-size: 14px; }
.c-id { font-size: 10px; color: #999; }
.empty-tip { text-align: center; color: #999; padding: 20px; font-size: 12px; }
.chart-area { flex: 1; display: flex; flex-direction: column; position: relative; }
.chart-header { padding: 12px 20px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; background: #fff; }
.title-group h2 { margin: 0; font-size: 18px; }
.sub-title { font-size: 12px; color: #999; }
.right-group { display: flex; align-items: center; gap: 15px; }
.analysis-btns { margin-right: 10px; } /* 新增 */
.indicators { display: flex; gap: 8px; }
.tv-chart { flex: 1; width: 100%; }
.chart-placeholder { position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; justify-content: center; align-items: center; background: #fff; z-index: 10; }
.floating-tooltip {
  width: 160px; position: absolute; display: none; padding: 8px; box-sizing: border-box; font-size: 12px; text-align: left; z-index: 1000; pointer-events: none; border: 1px solid #2962FF; background: rgba(255, 255, 255, 0.9); border-radius: 4px; box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1); font-family: 'Monaco', 'Consolas', monospace; line-height: 1.6;
}

/* 报告样式 */
.report-title { display: flex; align-items: center; width: 100%; }
.time-range { margin-left: 10px; font-family: monospace; color: #666; }
.change-tag { margin-left: auto; margin-right: 20px; font-size: 12px; color: #333; }
.report-content { padding: 10px 5px; }
.conclusion-box { background: #f0f9eb; padding: 15px; border-radius: 6px; border-left: 4px solid #67c23a; margin-bottom: 20px; }
.conclusion-box.risk-high { background: #fef0f0; border-left-color: #f56c6c; }
.conclusion-box h4 { margin: 0 0 8px 0; font-size: 14px; color: #333; }
.conclusion-box p { margin: 0; font-size: 13px; color: #606266; }

.metrics-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 20px; }
.metric-card { background: #f8f9fa; padding: 15px; border-radius: 6px; text-align: center; border: 1px solid #eee; }
.metric-card .label { font-size: 12px; color: #999; margin-bottom: 5px; }
.metric-card .value { font-size: 24px; font-weight: bold; margin-bottom: 5px; font-family: 'DIN', sans-serif; }
.metric-card .desc { font-size: 10px; color: #ccc; }
.text-red { color: #f56c6c; }
.text-orange { color: #e6a23c; }
.text-green { color: #67c23a; }

.large-orders h4 { margin: 0 0 10px 0; font-size: 14px; color: #333; }
</style>