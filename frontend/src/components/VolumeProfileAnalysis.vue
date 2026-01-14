<template>
  <div class="profile-analysis">
    <div class="control-panel">
      <h2>成交进度微观分析 (Intraday Progress)</h2>
      <div class="filters">
        <label>区域: 
          <select v-model="area" class="input-select">
            <option>SE1</option>
            <option>SE2</option>
            <option>SE3</option>
            <option>SE4</option>
          </select>
        </label>
        
        <label>开始日期: 
          <input type="date" v-model="startDate" class="input-date" />
        </label>
        
        <label>结束日期: 
          <input type="date" v-model="endDate" class="input-date" />
        </label>
        
        <label>合约代码: 
          <input v-model="shortName" placeholder="例如 PH01" class="input-short" />
        </label>
        
        <button @click="fetchData" :disabled="loading" class="btn-primary">
          {{ loading ? '分析中...' : '开始分析' }}
        </button>
      </div>
    </div>

    <div class="chart-container">
      <div ref="mainChartRef" class="echart-box"></div>
      
      <div v-if="loading" class="loading-mask">数据加载中...</div>
      <div v-if="!chartData && !loading" class="placeholder">
        请输入参数并点击"开始分析"
      </div>
    </div>

    <div v-if="showModal" class="modal-overlay" @click.self="closeModal">
      <div class="modal-content">
        <div class="modal-header">
          <h3>收盘前 {{ Math.abs(selectedTimeOffset) }} 分钟 ({{ selectedTimeOffset }}m) 分布详情</h3>
          <button @click="closeModal" class="close-btn">×</button>
        </div>
        <div class="modal-body">
          <div ref="distChartRef" class="dist-chart-box"></div>
          <div class="stats-info">
            <p><strong>样本数:</strong> {{ selectedRawData.length }} 天</p>
            <p><strong>中位数进度:</strong> {{ (selectedMedian * 100).toFixed(1) }}%</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, onUnmounted } from 'vue';
import * as echarts from 'echarts';
import { analyzeVolumeProfile } from '../api/service'; 
import dayjs from 'dayjs';

// 默认参数
const area = ref('SE3');
const shortName = ref('PH01');
// 默认查最近3个月
const endDate = ref(dayjs().format('YYYY-MM-DD'));
const startDate = ref(dayjs().subtract(3, 'month').format('YYYY-MM-DD'));

const loading = ref(false);
const chartData = ref(null);

const mainChartRef = ref(null);
const distChartRef = ref(null);
let myChart = null;
let myDistChart = null;

// 弹窗状态
const showModal = ref(false);
const selectedTimeOffset = ref(0);
const selectedRawData = ref([]);
const selectedMedian = ref(0);

// --- 核心修复：数据抓取 ---
const fetchData = async () => {
  if (!shortName.value) return alert("请输入合约代码");
  
  loading.value = true;
  chartData.value = null; // 清空旧数据
  
  try {
    const res = await analyzeVolumeProfile({
      area: area.value,
      short_name: shortName.value,
      start_date: startDate.value,
      end_date: endDate.value
    });
    
    // 【关键修复】Axios 返回结构解包
    // res.data 才是服务器返回的 JSON: { status: "success", data: {...} }
    const serverJson = res.data; 
    
    if (serverJson.status === 'success') {
      chartData.value = serverJson.data; // 这里拿到 data 内部的 { timeline: [...] }
      
      // 确保 DOM 更新后再渲染图表
      await nextTick();
      renderMainChart();
    } else {
      alert("分析失败: " + (serverJson.msg || '未知错误'));
    }
  } catch (e) {
    console.error(e);
    alert("请求异常: " + e.message);
  } finally {
    loading.value = false;
  }
};

const initMainChart = () => {
  if (myChart) {
    myChart.dispose();
  }
  // 确保 DOM 存在
  if (mainChartRef.value) {
    myChart = echarts.init(mainChartRef.value);
    myChart.on('click', function(params) {
      if (params.componentType === 'series') {
        const pointData = chartData.value.timeline[params.dataIndex];
        openDistributionModal(pointData);
      }
    });
  }
};

const renderMainChart = () => {
  if (!myChart) initMainChart();
  if (!myChart) return; // 防御性编程

  const timeline = chartData.value.timeline;
  const xData = timeline.map(item => item.time_offset);
  const yData = timeline.map(item => (item.value * 100).toFixed(1)); // 转百分比
  
  const option = {
    title: { 
      text: `${shortName.value} 成交进度中位数曲线`,
      subtext: `范围: ${chartData.value.date_range} (样本: ${chartData.value.sample_days}天)`,
      left: 'center'
    },
    tooltip: {
      trigger: 'axis',
      formatter: '{b}m: {c}%'
    },
    grid: { top: 80, right: 50, left: 60, bottom: 50 },
    xAxis: {
      type: 'category',
      name: '收盘倒计时(分)',
      data: xData,
      boundaryGap: false,
      axisLabel: { interval: 11 } // 大约每小时一个标
    },
    yAxis: {
      type: 'value',
      name: '累计进度 (%)',
      max: 100,
      min: 0
    },
    series: [
      {
        data: yData,
        type: 'line',
        smooth: true,
        showSymbol: false,
        symbolSize: 8,
        lineStyle: { width: 3, color: '#00b894' },
        itemStyle: { color: '#00b894' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(0, 184, 148, 0.5)' },
            { offset: 1, color: 'rgba(0, 184, 148, 0.0)' }
          ])
        }
      }
    ]
  };
  
  myChart.setOption(option);
};

const openDistributionModal = (pointData) => {
  selectedTimeOffset.value = pointData.time_offset;
  selectedRawData.value = pointData.raw_data;
  selectedMedian.value = pointData.value;
  showModal.value = true;
  
  nextTick(() => {
    renderDistChart();
  });
};

const closeModal = () => {
  showModal.value = false;
  if (myDistChart) {
    myDistChart.dispose();
    myDistChart = null;
  }
};

const renderDistChart = () => {
  // 注意：在弹窗里，ref 需要重新获取或确保存在
  if (!distChartRef.value) return;
  
  if (myDistChart) myDistChart.dispose();
  myDistChart = echarts.init(distChartRef.value);
  
  const raw = selectedRawData.value;
  // 分箱逻辑: 0-100%
  const bins = new Array(101).fill(0);
  
  raw.forEach(val => {
    let pct = Math.round(val * 100);
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    bins[pct]++;
  });
  
  const xData = bins.map((_, i) => `${i}%`);
  
  const option = {
    tooltip: {
      trigger: 'axis',
      formatter: function(params) {
        return `进度 ${params[0].name}: 出现 ${params[0].value} 次`;
      }
    },
    grid: { top: 30, right: 30, left: 50, bottom: 30 },
    xAxis: {
      type: 'category',
      data: xData,
      name: '进度',
      axisLabel: { interval: 9 }
    },
    yAxis: {
      type: 'value',
      name: '频次'
    },
    series: [
      {
        data: bins,
        type: 'bar',
        barWidth: '100%',
        itemStyle: { color: '#6c5ce7' },
        markLine: {
          data: [
            { name: '中位数', xAxis: (selectedMedian.value * 100).toFixed(0) + '%' }
          ],
          lineStyle: { color: '#ff7675', type: 'dashed', width: 2 },
          label: { show: true, position: 'end', formatter: '中位数' }
        }
      }
    ]
  };
  
  myDistChart.setOption(option);
};

// 窗口大小调整自适应
const handleResize = () => {
  myChart && myChart.resize();
  myDistChart && myDistChart.resize();
};

onMounted(() => {
  window.addEventListener('resize', handleResize);
});

onUnmounted(() => {
  window.removeEventListener('resize', handleResize);
  if (myChart) myChart.dispose();
  if (myDistChart) myDistChart.dispose();
});
</script>

<style scoped>
.profile-analysis {
  padding: 20px;
  background: #f8f9fa;
  min-height: 80vh;
}

.control-panel {
  background: white;
  padding: 20px;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.05);
  margin-bottom: 20px;
}

.filters {
  display: flex;
  gap: 15px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 15px;
}

.input-select { padding: 6px; border-radius: 4px; border: 1px solid #ddd; }
.input-date { padding: 5px; border: 1px solid #ddd; border-radius: 4px; }
.input-short { width: 80px; padding: 6px; border: 1px solid #ddd; border-radius: 4px; }

.btn-primary {
  background: #0984e3;
  color: white;
  border: none;
  padding: 8px 20px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.2s;
}
.btn-primary:hover { background: #0076d1; }
.btn-primary:disabled { background: #b2bec3; cursor: not-allowed; }

.chart-container {
  background: white;
  padding: 20px;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.05);
  height: 500px; /* 必须给高度 */
  position: relative;
}

.echart-box { width: 100%; height: 100%; }

.placeholder, .loading-mask { 
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); 
  color: #999; font-size: 16px;
  pointer-events: none;
}

/* Modal Styles */
.modal-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex; justify-content: center; align-items: center;
  z-index: 2000;
}

.modal-content {
  background: white;
  width: 900px;
  max-width: 95vw;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.2);
}

.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  border-bottom: 1px solid #eee; padding-bottom: 10px; margin-bottom: 20px;
}

.close-btn {
  background: none; border: none; font-size: 28px; cursor: pointer; color: #666;
}

.modal-body { height: 450px; }
.dist-chart-box { width: 100%; height: 400px; }
.stats-info { 
  display: flex; gap: 30px; justify-content: center; 
  font-size: 14px; color: #666; margin-top: 10px;
}
</style>