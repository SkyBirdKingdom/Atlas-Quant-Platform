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
            <span class="c-time">{{ c.label }}</span>
            <span class="c-id">{{ c.contract_id }}</span>
          </div>
          <el-tag size="small" :type="c.type === 'PH' ? '' : 'warning'" effect="plain">{{ c.type }}</el-tag>
        </div>
        <div v-if="contracts.length === 0" class="empty-tip">暂无合约数据</div>
      </div>
    </div>

    <div class="chart-area">
      <div class="chart-header" v-if="currentContract">
        <div class="title-group">
          <h2>{{ currentContract.label }} ({{ selectedArea }})</h2>
          <span class="sub-title">Delivery: {{ currentContract.delivery_time }} UTC</span>
        </div>
        
        <div class="right-group">
          <el-button type="primary" link icon="View" @click="openDebugDialog">
            查看原始 Trade
          </el-button>
          
          <div class="indicators">
            <el-tag type="info" effect="plain">O: {{ lastPrice.open }}</el-tag>
            <el-tag type="info" effect="plain">H: {{ lastPrice.high }}</el-tag>
            <el-tag type="info" effect="plain">L: {{ lastPrice.low }}</el-tag>
            <el-tag type="info" effect="plain">C: {{ lastPrice.close }}</el-tag>
            <el-tag color="#fff6e0" style="color: #ff9800; border: 1px solid #ffe0b2">VWAP: {{ lastPrice.vwap }}</el-tag>
          </div>
        </div>
      </div>
      
      <div ref="chartContainer" class="tv-chart">
      </div>
      <div ref="toolTip" class="floating-tooltip"></div>
      
      <div v-if="!currentContract" class="chart-placeholder">
        <el-empty description="请从左侧选择一个合约查看 K 线走势" />
      </div>
    </div>

    <el-dialog v-model="debugVisible" title="原始交易数据验光 (Raw Trades)" width="800px">
      <el-table :data="debugData" height="500" stripe style="width: 100%">
        <el-table-column prop="trade_id" label="Trade ID" width="120" />
        <el-table-column prop="time_utc" label="交易时间 (UTC)" sortable width="180">
          <template #default="scope">
            <b>{{ scope.row.time_utc }}</b>
          </template>
        </el-table-column>
        <el-table-column prop="price" label="价格 (EUR)" sortable />
        <el-table-column prop="volume" label="成交量 (MW)" sortable />
        <el-table-column prop="area" label="区域" width="80" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, markRaw } from 'vue';
import { createChart, CandlestickSeries, HistogramSeries, LineSeries, createSeriesMarkers } from 'lightweight-charts';
// 需要手动在 service.js 增加 getRawTrades 方法，或者直接用 axios 调用
import { getContracts, getCandles, getTradesbyContract } from '../api/service'; 
import { ElMessage } from 'element-plus';
import { Histogram, View } from '@element-plus/icons-vue';

// --- 状态 ---
const selectedArea = ref('SE3');
const selectedDate = ref('2025-12-01');
const contracts = ref([]);
const listLoading = ref(false);

const currentContractId = ref(null);
const currentContract = ref(null);
const lastPrice = ref({ open: '-', high: '-', low: '-', close: '-', vwap: '-' });

const toolTip = ref(null);

// --- Debug 相关 ---
const debugVisible = ref(false);
const debugData = ref([]);

// --- 图表实例 (普通变量) ---
const chartContainer = ref(null);
let chart = null;
let candleSeries = null;
let volumeSeries = null;
let vwapSeries = null;

// --- 辅助函数 ---
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

// --- 加载合约列表 ---
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

// --- Debug: 打开弹窗 ---
const openDebugDialog = async () => {
  if (!currentContractId.value) return;
  debugVisible.value = true;
  debugData.value = [];
  try {
    // 调用我们在 backend/main.py 新写的接口
    const res = await getTradesbyContract(selectedArea.value, currentContractId.value);
    debugData.value = res.data;
  } catch (e) {
    ElMessage.error("获取原始数据失败");
  }
};

// --- 选择合约 ---
const selectContract = async (c) => {
  currentContractId.value = c.contract_id;
  currentContract.value = c;
  
  try {
    const res = await getCandles(selectedArea.value, c.contract_id);
    const rawData = res.data;
    
    await nextTick();
    
    // ⭐ 关键策略：销毁旧图表，重建新图表 ⭐
    // 这能 100% 解决 Marker 残留问题
    resetChart();
    
    renderChart(rawData, c);
  } catch (e) {
    console.error(e);
    ElMessage.error("K线加载失败");
  }
};

// --- 销毁并重置图表 ---
const resetChart = () => {
  if (chart) {
    chart.remove(); // 物理销毁 DOM 和 实例
    chart = null;
    candleSeries = null;
    volumeSeries = null;
    vwapSeries = null;
  }
};

// --- 渲染图表 ---
const renderChart = (data, contractInfo) => {
  // 此时 chart 一定是 null，因为刚才 resetChart 了
  initChart(); 
  if (!chart || !candleSeries) return;

  const processedData = [...data];
  const openTs = contractInfo.open_ts;
  const closeTs = contractInfo.close_ts;

  // 补全逻辑：不强行截断，但确保有头有尾
  if (processedData.length > 0) {
    // 只有当所有数据都晚于开盘时间时，才补一个开盘空K
    if (processedData[0].time > openTs) {
        processedData.unshift({ time: openTs, open: processedData[0].open, high: processedData[0].open, low: processedData[0].open, close: processedData[0].open, volume: 0, vwap: processedData[0].vwap });
    }
    // 只有当所有数据都早于收盘时间时，才补一个收盘空K
    if (processedData[processedData.length-1].time < closeTs) {
        processedData.push({ time: closeTs, open: processedData[processedData.length-1].close, high: processedData[processedData.length-1].close, low: processedData[processedData.length-1].close, close: processedData[processedData.length-1].close, volume: 0, vwap: processedData[processedData.length-1].vwap });
    }
  } else {
     processedData.push({ time: openTs, open: 0, high: 0, low: 0, close: 0, volume: 0, vwap: 0 });
     processedData.push({ time: closeTs, open: 0, high: 0, low: 0, close: 0, volume: 0, vwap: 0 });
  }
  
  // 转换数据
  const candleData = processedData.map(d => ({ time: d.time, open: d.open, high: d.high, low: d.low, close: d.close }));
  const volumeData = processedData.map(d => ({ time: d.time, value: d.volume, color: d.close >= d.open ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)' }));
  const vwapData = processedData.map(d => ({ time: d.time, value: d.vwap > 0 ? d.vwap : undefined }));

  // 填充
  candleSeries.setData(candleData);
  volumeSeries.setData(volumeData);
  vwapSeries.setData(vwapData);
  
  // 设置 Markers (在新创建的 series 上设置，绝对不会重复)
  const markers = [];
  markers.push({ time: openTs, position: 'aboveBar', color: '#2196F3', shape: 'arrowDown', text: 'OPEN' });
  markers.push({ time: closeTs, position: 'aboveBar', color: '#E91E63', shape: 'arrowDown', text: 'CLOSE' });
  
  createSeriesMarkers(candleSeries, markers);
  
  chart.timeScale().fitContent();
  
  // 更新价格板
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

// --- 初始化 ---
const initChart = () => {
  if (!chartContainer.value) return;
  
  const chartInstance = createChart(chartContainer.value, {
    width: chartContainer.value.clientWidth,
    height: chartContainer.value.clientHeight,
    layout: { background: { color: '#ffffff' }, textColor: '#333' },
    grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
    rightPriceScale: { visible: true },
    // localization: { timeFormatter: formatTimeUtc, dateFormat: 'yyyy-MM-dd' },
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

  candleSeries = markRaw(chart.addSeries(CandlestickSeries, {
    upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350',
  }));

  vwapSeries = markRaw(chart.addSeries(LineSeries, {
    color: '#ff9800', lineWidth: 2, title: 'VWAP', crosshairMarkerVisible: false
  }));

  volumeSeries = markRaw(chart.addSeries(HistogramSeries, {
    priceFormat: { type: 'volume' }, priceScaleId: '', 
  }));
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

  chart.subscribeCrosshairMove(param => {
    if (!toolTip.value) return;

    if (
      param.point === undefined ||
      !param.time ||
      param.point.x < 0 ||
      param.point.x > chartContainer.value.clientWidth ||
      param.point.y < 0 ||
      param.point.y > chartContainer.value.clientHeight
    ) {
      // 鼠标离开图表区域，隐藏 Tooltip
      toolTip.value.style.display = 'none';
      return;
    }

    // 显示 Tooltip
    toolTip.value.style.display = 'block';
    
    // 获取各系列在当前时间点的数据
    // 使用 Map 获取数据: param.seriesData.get(seriesInstance)
    const candleData = param.seriesData.get(candleSeries);
    const vwapData = param.seriesData.get(vwapSeries);
    const volumeData = param.seriesData.get(volumeSeries);

    if (!candleData) {
        toolTip.value.style.display = 'none'; // 没数据也不显示
        return;
    }

    // 构造显示内容 HTML
    // 注意：toFixed(2) 保留两位小数
    const priceHtml = `
      <div style="color: #333; font-weight: bold; margin-bottom: 4px">${formatTimeUtc(param.time)} (UTC)</div>
      <div style="display: flex; justify-content: space-between;"><span>Open:</span> <span>${candleData.open.toFixed(2)}</span></div>
      <div style="display: flex; justify-content: space-between;"><span>High:</span> <span>${candleData.high.toFixed(2)}</span></div>
      <div style="display: flex; justify-content: space-between;"><span>Low:</span> <span>${candleData.low.toFixed(2)}</span></div>
      <div style="display: flex; justify-content: space-between;"><span>Close:</span> <span>${candleData.close.toFixed(2)}</span></div>
    `;

    // VWAP 可能为空 (如果该时刻没成交)
    const vwapVal = vwapData && vwapData.value ? vwapData.value.toFixed(2) : '-';
    const vwapHtml = `<div style="display: flex; justify-content: space-between; color: #ff9800"><span>VWAP:</span> <span>${vwapVal}</span></div>`;

    const volVal = volumeData && volumeData.value ? volumeData.value.toFixed(1) : '0';
    const volHtml = `<div style="display: flex; justify-content: space-between; color: #26a69a"><span>Vol:</span> <span>${volVal}</span></div>`;

    toolTip.value.innerHTML = priceHtml + vwapHtml + volHtml;

    // 智能定位：避免遮挡鼠标
    // 默认显示在鼠标右上方
    let left = param.point.x + 15;
    let top = param.point.y + 15;
    
    // 如果太靠右，就移到左边
    if (left + 160 > chartContainer.value.clientWidth) {
        left = param.point.x - 175;
    }
    // 如果太靠下，就移到上面
    if (top + 150 > chartContainer.value.clientHeight) {
        top = param.point.y - 160;
    }

    toolTip.value.style.left = left + 'px';
    toolTip.value.style.top = top + 'px';
  });
};

const handleResize = () => {
  if (chart && chartContainer.value) {
    chart.applyOptions({ width: chartContainer.value.clientWidth, height: chartContainer.value.clientHeight });
  }
};

onMounted(() => {
  loadContracts();
  window.addEventListener('resize', handleResize);
});

onUnmounted(() => {
  resetChart(); // 离开页面时销毁
  window.removeEventListener('resize', handleResize);
});
</script>

<style scoped>
/* 样式复用之前的，省略以节省篇幅，保持不变 */
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
.right-group { display: flex; align-items: center; gap: 15px; } /* 新增 */
.indicators { display: flex; gap: 8px; }
.tv-chart { flex: 1; width: 100%; }
.chart-placeholder { position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; justify-content: center; align-items: center; background: #fff; z-index: 10; }
.floating-tooltip {
  width: 160px;
  position: absolute;
  display: none; /* 默认隐藏 */
  padding: 8px;
  box-sizing: border-box;
  font-size: 12px;
  text-align: left;
  z-index: 1000;
  top: 12px;
  left: 12px;
  pointer-events: none; /* 让鼠标事件穿透，不影响图表操作 */
  border: 1px solid #2962FF;
  background: rgba(255, 255, 255, 0.9);
  border-radius: 4px;
  box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);
  font-family: 'Monaco', 'Consolas', monospace; /* 等宽字体对齐更好看 */
  line-height: 1.6;
}
</style>