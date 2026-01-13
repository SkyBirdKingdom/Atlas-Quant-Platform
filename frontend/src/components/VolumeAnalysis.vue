<template>
  <div class="va-container">
    <div class="sidebar">
      <div class="sidebar-header">
        <el-icon><TrendCharts /></el-icon>
        <h3>交易量 & 筹码分析</h3>
      </div>
      
      <div class="control-panel">
        <el-form label-position="top" size="default">
          <el-form-item label="交易区域">
            <el-radio-group v-model="area" style="width: 100%">
              <el-radio-button label="SE1" />
              <el-radio-button label="SE2" />
              <el-radio-button label="SE3" />
              <el-radio-button label="SE4" />
            </el-radio-group>
          </el-form-item>

          <el-form-item label="合约简称">
            <el-input v-model="shortName" placeholder="e.g. PH11" clearable>
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
          </el-form-item>

          <el-form-item label="日期范围">
            <el-date-picker
              v-model="dateRange"
              type="daterange"
              range-separator="至"
              start-placeholder="Start"
              end-placeholder="End"
              value-format="YYYY-MM-DD"
              style="width: 100%"
            />
          </el-form-item>

          <el-divider content-position="center">策略参数</el-divider>

          <div class="strategy-row">
            <el-form-item label="收盘前 N 小时 (Hours)">
              <el-input-number v-model="hoursBeforeClose" :min="0" :step="0.5" style="width: 100%" placeholder="不限" />
            </el-form-item>
            <el-form-item label="聚合点阈值 (M)">
              <el-input-number v-model="minPoints" :min="0" :step="1" style="width: 100%" placeholder="不限" />
            </el-form-item>
          </div>
          <div class="tip-text small">
            * 仅统计收盘前 {{ hoursBeforeClose || 'All' }} 小时，且满足 {{ minPoints || 0 }} 分钟有成交后的累积量。
          </div>

          <el-divider content-position="center">分析模式</el-divider>

          <el-form-item>
            <el-radio-group v-model="analysisType" style="width: 100%">
              <el-radio-button label="trend">趋势</el-radio-button>
              <el-radio-button label="intraday">日内</el-radio-button>
              <el-radio-button label="profile">筹码</el-radio-button>
            </el-radio-group>
          </el-form-item>

          <el-button type="primary" class="analyze-btn" @click="fetchData" :loading="loading" style="width: 100%">
            <el-icon><DataLine /></el-icon> 执行分析
          </el-button>
        </el-form>
      </div>
    </div>

    <div class="chart-wrapper">
      <div class="chart-header">
        <div class="chart-title">
          <h2>
            {{ shortName }}
            <span v-if="analysisType==='trend'">策略交易量趋势 (N={{hoursBeforeClose}}, M={{minPoints}})</span>
            <span v-else-if="analysisType==='intraday'">日内流动性分布</span>
            <span v-else>价格筹码分布 (Volume Profile)</span>
          </h2>
          <span class="sub-title">{{ area }} | {{ dateRange[0] }} ~ {{ dateRange[1] }}</span>
        </div>
      </div>
      <div class="chat-container" v-if="chartData.length === 0 && !loading">
        <el-empty class="empty-placeholder" description="暂无数据， 请填写查询参数并执行分析。">
        </el-empty>
      </div>
      <div ref="chartContainer" class="chart-container" v-else></div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { createChart, AreaSeries, HistogramSeries, createSeriesMarkers } from 'lightweight-charts';
import { getVolumeTrend, getIntradayPattern, getVolumeProfile } from '../api/service';
import { ElMessage } from 'element-plus';
import { TrendCharts, Search, DataLine } from '@element-plus/icons-vue';

const area = ref('SE3');
const shortName = ref('PH11');
const dateRange = ref(['2025-10-01', '2025-10-31']);
const analysisType = ref('trend');

// 新增策略参数
const hoursBeforeClose = ref(4.0); // 默认收盘前4小时
const minPoints = ref(10);          // 默认需要10个活跃分钟

const loading = ref(false);
const chartData = ref([]);

const chartContainer = ref(null);
let chart = null;
let series = null;

const fetchData = async () => {
  if (!shortName.value || !dateRange.value) {
    ElMessage.warning("请填写完整的查询参数");
    return;
  }

  loading.value = true;
  chartData.value = [];
  
  try {
    const payload = {
      area: area.value,
      short_name: shortName.value,
      start_date: dateRange.value[0],
      end_date: dateRange.value[1],
      // 传递新参数 (仅在 trend 模式下生效，但传给后端无妨)
      hours_before_close: hoursBeforeClose.value || null,
      min_points: minPoints.value || 0
    };

    let res;
    if (analysisType.value === 'trend') {
      res = await getVolumeTrend(payload);
    } else if (analysisType.value === 'intraday') {
      res = await getIntradayPattern(payload);
    } else if (analysisType.value === 'profile') {
      res = await getVolumeProfile(payload);
    }

    const rawData = res.data && Array.isArray(res.data.data) ? res.data.data : (Array.isArray(res.data) ? res.data : []);
    chartData.value = rawData;
    renderChart(rawData);
    
    if (rawData.length === 0) {
      ElMessage.info("该条件下无有效数据 (可能未满足 M 个聚合点)");
    } else {
      ElMessage.success(`分析完成，加载 ${rawData.length} 条数据`);
    }
  } catch (e) {
    console.error(e);
    ElMessage.error("分析失败: " + (e.response?.data?.detail || e.message));
  } finally {
    loading.value = false;
  }
};

const renderChart = (data) => {
  if (!chartContainer.value) return;
  
  if (chart) {
    chart.remove();
    chart = null;
  }

  chart = createChart(chartContainer.value, {
    width: chartContainer.value.clientWidth,
    height: chartContainer.value.clientHeight,
    layout: { background: { color: '#ffffff' }, textColor: '#333' },
    grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
    rightPriceScale: { visible: true, borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true }
  });

  if (analysisType.value === 'trend') {
    series = chart.addSeries(AreaSeries, {
      lineColor: '#2962FF',
      topColor: 'rgba(41, 98, 255, 0.3)', 
      bottomColor: 'rgba(41, 98, 255, 0.0)',
      lineWidth: 2,
    });
    series.setData(data);
    setExtremesMarkers(data);

  } else if (analysisType.value === 'intraday') {
    const formattedData = data.map(d => ({
       time: (new Date(2000, 0, 1, 0, d.minute).getTime() / 1000), 
       value: d.volume, 
       color: '#26a69a'
    }));
    chart.applyOptions({
        timeScale: {
            tickMarkFormatter: (time) => (new Date(time * 1000)).getMinutes() + 'm'
        }
    });
    series = chart.addSeries(HistogramSeries, { color: '#26a69a' });
    series.setData(formattedData);

  } else if (analysisType.value === 'profile') {
    const sortedData = [...data].sort((a, b) => a.price - b.price);
    const baseTime = new Date(2020, 0, 1).getTime() / 1000;
    const formattedData = sortedData.map((d, i) => ({
        time: baseTime + i * 3600,
        value: d.volume,
        originalPrice: d.price,
        color: '#ff9800'
    }));
    chart.applyOptions({
        timeScale: {
            tickMarkFormatter: (time) => {
                const item = formattedData.find(f => f.time === time);
                return item ? item.originalPrice.toFixed(1) : '';
            }
        }
    });
    series = chart.addSeries(HistogramSeries, { color: '#ff9800' });
    series.setData(formattedData);
  }

  chart.timeScale().fitContent();
};

const setExtremesMarkers = (data) => {
  if (data.length === 0) return;
  let maxObj = data[0], minObj = data[0];
  for (const item of data) {
    if (item.value > maxObj.value) maxObj = item;
    if (item.value < minObj.value) minObj = item;
  }
  const markers = [{
      time: maxObj.time, position: 'aboveBar', color: '#e91e63', shape: 'arrowDown', text: `MAX: ${maxObj.value}`
  }];
  if (minObj.time !== maxObj.time) {
    markers.push({
      time: minObj.time, position: 'belowBar', color: '#2196f3', shape: 'arrowUp', text: `MIN: ${minObj.value}`
    });
  }
  // series.setMarkers(markers);
  createSeriesMarkers(series, markers);
};

const handleResize = () => {
  if (chart && chartContainer.value) {
    chart.applyOptions({ width: chartContainer.value.clientWidth, height: chartContainer.value.clientHeight });
  }
};

onMounted(() => { window.addEventListener('resize', handleResize); });
onUnmounted(() => { if (chart) { chart.remove(); } window.removeEventListener('resize', handleResize); });
</script>

<style scoped>
.va-container { display: flex; height: 800px; background: #fff; border: 1px solid #e0e0e0; border-radius: 4px; }
.sidebar { width: 320px; border-right: 1px solid #eee; padding: 20px; display: flex; flex-direction: column; background: #fcfcfc; }
.sidebar-header { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; color: #333; }
.sidebar-header h3 { margin: 0; font-size: 18px; }
.control-panel { flex: 1; display: flex; flex-direction: column; gap: 12px; }
.strategy-row { display: flex; gap: 10px; }
.analyze-btn { margin-top: 10px; height: 40px; font-size: 16px; }
.tip-text { font-size: 12px; color: #888; background: #f0f0f0; padding: 8px; border-radius: 4px; line-height: 1.4; }
.tip-text.small { font-size: 11px; color: #666; padding: 5px; }

.chart-wrapper { flex: 1; display: flex; flex-direction: column; }
.chart-header { height: 60px; border-bottom: 1px solid #eee; display: flex; align-items: center; justify-content: center; background: #fff; }
.chart-title { text-align: center; }
.chart-title h2 { margin: 0; font-size: 18px; color: #333; }
.sub-title { font-size: 12px; color: #666; }
.chart-container { flex: 1; width: 100%; position: relative; }
.empty-placeholder { display: flex; align-items: center; justify-content: center; height: 100%; }
</style>