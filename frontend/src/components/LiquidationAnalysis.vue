<template>
  <div class="liquidation-analysis">
    <div class="control-panel">
      <h2>清算模型准确性分析 (TTL Backtest)</h2>
      <div class="filters">
        <div class="filter-item">
          <span class="label">合约简称</span>
          <input v-model="shortName" placeholder="PH01" class="input-text" />
        </div>
        <div class="filter-item">
          <span class="label">日期范围</span>
          <input type="date" v-model="startDate" class="input-date" />
          <span class="sep">至</span>
          <input type="date" v-model="endDate" class="input-date" />
        </div>
        <button @click="fetchData" :disabled="loading" class="btn-primary">
          {{ loading ? '计算中...' : '开始回测' }}
        </button>
      </div>
    </div>

    <div class="chart-container">
      <div ref="chartRef" class="echart-box"></div>
    </div>

    <div class="table-container">
      <h3>详细数据 (Top/Bottom 标记)</h3>
      <el-table 
        :data="tableData" 
        style="width: 100%" 
        :row-class-name="tableRowClassName" 
        height="500"
        border
      >
        <el-table-column prop="delivery_date" label="交付日期" width="120" sortable />
        <el-table-column label="标记时间 (Marker)" width="160">
          <template #default="scope">
            {{ formatTime(scope.row.marker_time) }}
          </template>
        </el-table-column>
        <el-table-column prop="avg_flow_rate" label="流速 (MW/min)" width="120" />
        <el-table-column prop="projected_vol" label="推断总交易量" width="140" />
        <el-table-column prop="actual_vol" label="真实总交易量" width="140" />
        <el-table-column label="偏差百分比 (Proj/Act)" sortable prop="percentage">
          <template #default="scope">
            <span :class="getPercentClass(scope.row.percentage)">
              {{ scope.row.percentage }}%
            </span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue';
import * as echarts from 'echarts';
import { getLiquidationAnalysis } from '../api/service';
import dayjs from 'dayjs';

const shortName = ref('PH01');
const startDate = ref(dayjs().subtract(1, 'month').format('YYYY-MM-DD'));
const endDate = ref(dayjs().format('YYYY-MM-DD'));
const loading = ref(false);
const tableData = ref([]);

const chartRef = ref(null);
let myChart = null;

// 记录最大最小值的索引，用于高亮
let maxIdx = -1;
let minIdx = -1;

const fetchData = async () => {
  if(!shortName.value) return;
  loading.value = true;
  try {
    const res = await getLiquidationAnalysis({
      area: 'SE3',
      short_name: shortName.value,
      start_date: startDate.value,
      end_date: endDate.value
    });
    
    if (res.data.status === 'success') {
      const raw = res.data.data;
      tableData.value = raw;
      calculateStats(raw);
      renderChart(raw);
    }
  } catch(e) {
    alert("分析失败: " + e.message);
  } finally {
    loading.value = false;
  }
};

const calculateStats = (data) => {
  if (data.length === 0) return;
  
  let maxVal = -Infinity;
  let minVal = Infinity;
  
  data.forEach((item, index) => {
    if (item.percentage > maxVal) {
      maxVal = item.percentage;
      maxIdx = index;
    }
    if (item.percentage < minVal) {
      minVal = item.percentage;
      minIdx = index;
    }
  });
};

const renderChart = (data) => {
  if (myChart) myChart.dispose();
  myChart = echarts.init(chartRef.value);
  
  const dates = data.map(i => i.delivery_date);
  const values = data.map(i => i.percentage);
  
  const option = {
    title: { text: '推断 vs 真实交易量百分比分布', left: 'center' },
    tooltip: {
      trigger: 'axis',
      formatter: '{b}: {c}%'
    },
    grid: { top: 50, bottom: 30, left: 50, right: 30 },
    xAxis: { data: dates },
    yAxis: { 
      name: '百分比 (%)',
      // 添加基准线 100%
      axisLine: { show: true },
      splitLine: { show: true }
    },
    series: [{
      type: 'line', // 使用折线图更能看清趋势，也可以改成 bar
      data: values,
      markLine: {
        data: [{ yAxis: 100, name: '100% (准确)' }],
        lineStyle: { color: '#00b894', type: 'dashed' }
      },
      markPoint: {
        data: [
          { type: 'max', name: '最大偏差' },
          { type: 'min', name: '最小偏差' }
        ]
      },
      itemStyle: { color: '#0984e3' }
    }]
  };
  myChart.setOption(option);
};

// 表格样式辅助
const formatTime = (isoStr) => dayjs(isoStr).format('HH:mm:ss');

const tableRowClassName = ({ row, rowIndex }) => {
  if (rowIndex === maxIdx) return 'warning-row';
  if (rowIndex === minIdx) return 'success-row';
  return '';
};

const getPercentClass = (val) => {
  if (val > 150) return 'text-red';
  if (val < 50) return 'text-blue';
  return 'text-normal';
};

// 响应式图表
const resizeChart = () => myChart && myChart.resize();
onMounted(() => window.addEventListener('resize', resizeChart));
onUnmounted(() => window.removeEventListener('resize', resizeChart));
</script>

<style>
.liquidation-analysis {
  padding: 20px;
  background: #f4f6f8;
  min-height: 80vh;
}
.control-panel {
  background: white;
  padding: 20px;
  border-radius: 8px;
  margin-bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 15px;
}
.filters {
  display: flex;
  gap: 20px;
  align-items: flex-end;
}
.filter-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.label { font-size: 12px; color: #666; font-weight: bold; }
.input-text { padding: 8px; border: 1px solid #ddd; border-radius: 4px; width: 100px; }
.input-date { padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
.sep { margin: 0 5px; color: #999; }
.btn-primary {
  padding: 8px 24px;
  background: #2c3e50;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  height: 36px;
}
.chart-container {
  height: 400px;
  background: white;
  padding: 20px;
  border-radius: 8px;
  margin-bottom: 20px;
}
.echart-box { width: 100%; height: 100%; }
.table-container {
  background: white;
  padding: 20px;
  border-radius: 8px;
}
.text-red { color: #e74c3c; font-weight: bold; }
.text-blue { color: #3498db; font-weight: bold; }

/* Element Plus Table Highlight Rows */
.el-table .warning-row {
  background: #fdf6ec; /* 橙色背景标记最大值 */
}
.el-table .success-row {
  background: #f0f9eb; /* 绿色背景标记最小值 */
}
</style>