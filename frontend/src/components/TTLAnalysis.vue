<template>
  <div class="ttl-analysis">
    <div class="control-panel">
      <h2>TTL 模型回测验证 (Model Verification)</h2>
      <div class="params-row">
        <label>合约: <input v-model="shortName" class="s-input" placeholder="PH01"/></label>
        <label>日期: <input type="date" v-model="startDate" class="date-input"/> - <input type="date" v-model="endDate" class="date-input"/></label>
      </div>
      <div class="params-row advanced">
        <label>回看窗口 (测流速): 
          <input type="number" v-model="lookback" class="n-input"/> 分钟
        </label>
        <label>置信上限 (Horizon): 
          <input type="number" v-model="horizon" class="n-input"/> 分钟
        </label>
        <el-button @click="runVerify" :loading="loading" class="btn-run">运行验证</el-button>
      </div>
    </div>

    <div class="chart-box" ref="scatterRef"></div>

    <div class="table-box">
      <h3>每日风险评估 (Danger % = 预测 > 真实的占比)</h3>
      <el-table :data="tableData" border height="400" :row-class-name="tableRowClass">
        <el-table-column prop="date" label="日期" sortable width="120" />
        <el-table-column prop="avg_flow" label="平均流速 (MW/min)" width="150" />
        <el-table-column prop="danger_pct" label="危险时间占比 (%)" sortable>
          <template #default="scope">
            <span :class="scope.row.danger_pct > 20 ? 'text-danger' : 'text-safe'">
              {{ scope.row.danger_pct }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="max_ratio" label="最大高估比 (%)" sortable width="150" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import * as echarts from 'echarts';
import { verifyTTLModel } from '../api/service';
import dayjs from 'dayjs';

const shortName = ref('PH01');
const startDate = ref(dayjs().subtract(1, 'month').format('YYYY-MM-DD'));
const endDate = ref(dayjs().format('YYYY-MM-DD'));
const lookback = ref(15);
const horizon = ref(60);
const loading = ref(false);

const tableData = ref([]);
const scatterRef = ref(null);
let myChart = null;

const runVerify = async () => {
  loading.value = true;
  try {
    const res = await verifyTTLModel({
      area: 'SE3',
      short_name: shortName.value,
      start_date: startDate.value,
      end_date: endDate.value,
      lookback_minutes: Number(lookback.value),
      horizon_cap: Number(horizon.value)
    });
    
    if (res.data.status === 'success') {
      const { daily_stats, scatter_points } = res.data.data;
      tableData.value = daily_stats;
      renderScatter(scatter_points);
    }
  } catch (e) {
    alert("验证失败: " + e.message);
  } finally {
    loading.value = false;
  }
};

const renderScatter = (points) => {
  if (myChart) myChart.dispose();
  myChart = echarts.init(scatterRef.value);
  
  // ECharts Scatter Format: [x, y]
  // X: mins_to_close (倒序显示比较直观), Y: ratio
  const data = points.map(p => [p.mins_to_close, p.ratio]);
  
  const option = {
    title: { text: '预测准确度分布 (100%线以下为安全)', left: 'center' },
    tooltip: {
      formatter: (params) => {
        return `收盘前 ${params.value[0]}分<br/>预测/真实: ${params.value[1]}%`;
      }
    },
    xAxis: { 
      name: '距离收盘(分)', 
      type: 'value', 
      inverse: true, // 倒计时习惯
      min: 0, max: 240 
    },
    yAxis: { 
      name: '高估比例 (%)', 
      type: 'value',
      max: 300 // 截断一下，防止无穷大点压缩视图
    },
    visualMap: {
      min: 0, max: 200,
      dimension: 1, // Color based on Y (Ratio)
      inRange: { color: ['#00b894', '#fdcb6e', '#d63031'] },
      pieces: [
        { lte: 100, color: '#00b894', label: '安全区' },
        { gt: 100, color: '#d63031', label: '危险区' }
      ]
    },
    series: [{
      type: 'scatter',
      symbolSize: 6,
      data: data,
      markLine: {
        data: [{ yAxis: 100, name: '1:1 基准线' }],
        lineStyle: { type: 'solid', color: '#333', width: 2 }
      }
    }]
  };
  myChart.setOption(option);
};

const tableRowClass = ({ row }) => {
  return row.danger_pct > 20 ? 'row-danger' : '';
};

const handleResize = () => myChart && myChart.resize();
onMounted(() => window.addEventListener('resize', handleResize));
onUnmounted(() => window.removeEventListener('resize', handleResize));
</script>

<style scoped>
.ttl-analysis { padding: 20px; background: #f4f7f6; min-height: 80vh; }
.control-panel { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
.params-row { display: flex; gap: 20px; margin-bottom: 10px; align-items: center; }
.advanced { background: #f8f9fa; padding: 10px; border-radius: 4px; }
.s-input { padding: 6px; width: 80px; border: 1px solid #ddd; border-radius: 4px; }
.n-input { padding: 6px; width: 60px; border: 1px solid #ddd; border-radius: 4px; }
.date-input { padding: 6px; border: 1px solid #ddd; border-radius: 4px; }
.btn-run { background: #6c5ce7; color: white; border: none; padding: 8px 20px; border-radius: 4px; cursor: pointer; }
.chart-box { height: 400px; background: white; margin-bottom: 20px; padding: 10px; border-radius: 8px; }
.table-box { background: white; padding: 20px; border-radius: 8px; }
.text-danger { color: #d63031; font-weight: bold; }
.text-safe { color: #00b894; font-weight: bold; }
</style>