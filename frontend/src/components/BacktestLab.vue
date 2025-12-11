<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <div class="header-title">
          <el-icon><TrendCharts /></el-icon>
          <span>策略回测实验室 (Strategy Lab)</span>
        </div>
      </div>
    </template>

    <div class="lab-container">
      <div class="config-panel">
        <el-form label-position="top" size="small">
          <el-form-item label="交易区域">
            <el-radio-group v-model="form.area" size="small" style="width: 100%">
              <el-radio-button label="SE1" />
              <el-radio-button label="SE2" />
              <el-radio-button label="SE3" />
              <el-radio-button label="SE4" />
            </el-radio-group>
          </el-form-item>
          <el-form-item label="回测区间">
            <el-date-picker
              v-model="form.range"
              type="daterange"
              range-separator="-"
              start-placeholder="Start"
              end-placeholder="End"
              value-format="YYYY-MM-DD"
              style="width: 100%"
            />
          </el-form-item>

          <el-divider>策略参数</el-divider>

          <el-form-item label="满仓额度 (MW)">
            <el-input-number v-model="form.basePos" :min="1" />
          </el-form-item>
          <el-form-item label="降级额度 (MW)">
            <el-input-number v-model="form.reducedPos" :min="0" />
          </el-form-item>

          <el-divider>风控阈值</el-divider>
          
          <el-form-item :label="`PH 阈值: ${form.phLimit} MW`">
            <el-slider v-model="form.phLimit" :max="100" />
          </el-form-item>
          
          <el-form-item :label="`QH 阈值: ${form.qhLimit} MW`">
            <el-slider v-model="form.qhLimit" :max="50" />
          </el-form-item>

          <el-button type="primary" size="large" style="width: 100%; margin-top: 20px" @click="runTest" :loading="loading">
            🚀 开始回测
          </el-button>
        </el-form>
      </div>

      <div class="result-panel">
        <el-row :gutter="20" class="kpi-row" v-if="summary">
          <el-col :span="6">
            <el-statistic title="累计节省成本 (EUR)" :value="summary.total_saved" value-style="color: #67c23a; font-weight: bold" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="风控优化率 (ROI)" :value="summary.roi_improvement" suffix="%" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="触发降级次数" :value="summary.downgrade_count" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="原始潜在滑点成本" :value="summary.total_naive_cost" />
          </el-col>
        </el-row>

        <div ref="chartRef" style="width: 100%; height: 450px; flex: 1;"></div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';
import * as echarts from 'echarts';
import { runBacktest } from '../api/service';
import { TrendCharts } from '@element-plus/icons-vue';

const loading = ref(false);
const chartRef = ref(null);
let myChart = null;

const form = reactive({
  area: 'SE3',
  range: ['2025-12-01', '2025-12-07'],
  basePos: 5.0,
  reducedPos: 2.0,
  phLimit: 40,
  qhLimit: 10
});

const summary = ref(null);

const runTest = async () => {
  if (!form.range) return;
  loading.value = true;
  
  try {
    const res = await runBacktest({
      start_date: form.range[0],
      end_date: form.range[1],
      area: form.area,
      ph_threshold: form.phLimit,
      qh_threshold: form.qhLimit,
      base_pos: form.basePos,
      reduced_pos: form.reducedPos
    });
    
    const data = res.data.data;
    summary.value = data.summary;
    renderChart(data.chart);
    
  } catch (e) {
    console.error(e);
  } finally {
    loading.value = false;
  }
};

const renderChart = (chartData) => {
  if (!myChart) myChart = echarts.init(chartRef.value);
  
  // 提取数据
  const times = chartData.map(i => i.time);
  const savings = chartData.map(i => i.cumulative);
  const dailys = chartData.map(i => i.saved);

  const option = {
    title: { text: '风控策略价值曲线', left: 'center' },
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, data: ['累计节省 (Cumulative)', '单次节省 (Instant)'] },
    grid: { left: '3%', right: '4%', bottom: '10%', containLabel: true },
    xAxis: { type: 'category', data: times, axisLabel: { rotate: 30 } },
    yAxis: [
        { type: 'value', name: '累计节省 (€)', position: 'left' },
        { type: 'value', name: '单次 (€)', position: 'right', splitLine: { show: false } }
    ],
    series: [
      {
        name: '累计节省 (Cumulative)',
        type: 'line',
        data: savings,
        smooth: true,
        areaStyle: { opacity: 0.3, color: '#67c23a' },
        lineStyle: { color: '#67c23a', width: 3 },
        yAxisIndex: 0
      },
      {
        name: '单次节省 (Instant)',
        type: 'bar',
        data: dailys,
        itemStyle: { color: '#409eff' },
        yAxisIndex: 1
      }
    ]
  };
  
  myChart.setOption(option);
};

onMounted(() => {
    // 默认跑一次
    runTest();
    window.addEventListener('resize', () => myChart && myChart.resize());
});
</script>

<style scoped>
.lab-container {
  display: flex;
  gap: 20px;
  height: 600px; /* 1. 给个固定总高度，防止塌陷 */
}

.config-panel {
  width: 320px; /* 2. 左侧固定宽度稍微加宽 */
  background: #f8f9fa;
  padding: 20px;
  border-radius: 8px;
  height: 100%; /* 撑满高度 */
  overflow-y: auto; /* 内容多时可滚动 */
  flex-shrink: 0; /* 防止被挤压 */
}

.result-panel {
  flex: 1; /* 3. 右侧自动撑满剩余空间 */
  display: flex;
  flex-direction: column;
  min-width: 0; /* 4. 关键！防止 Flex 子元素内容溢出导致布局错乱 */
}

.kpi-row {
  margin-bottom: 20px;
  background: #f0f9eb;
  padding: 20px;
  border-radius: 8px;
  border: 1px solid #c2e7b0;
}
</style>