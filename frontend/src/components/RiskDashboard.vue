<template>
  <div class="dashboard-container">
    <el-card class="box-card">
      <template #header>
        <div class="card-header">
          <h2>⚡ 单日深度透视 ({{ currentArea }})</h2>
          
          <div class="admin-actions">
            <el-popconfirm 
              :title="`确定要重新抓取 ${currentArea} 的数据吗？`"
              @confirm="handleFetchData"
            >
              <template #reference>
                <el-button type="primary" link :loading="fetching">
                  🔄 同步 {{ currentArea }} 数据
                </el-button>
              </template>
            </el-popconfirm>
          </div>
        </div>
      </template>

      <div class="control-panel">
        <el-form :inline="true" size="default">
          <el-form-item label="区域">
            <el-radio-group v-model="currentArea" @change="loadAnalysisData">
              <el-radio-button label="SE1" />
              <el-radio-button label="SE2" />
              <el-radio-button label="SE3" />
              <el-radio-button label="SE4" />
            </el-radio-group>
          </el-form-item>

          <el-divider direction="vertical" />

          <el-form-item label="分析日期">
            <el-date-picker
              v-model="queryDate"
              type="date"
              placeholder="选择日期"
              value-format="YYYY-MM-DD"
              :clearable="false"
              @change="loadAnalysisData"
              style="width: 150px;"
            />
          </el-form-item>

          <el-form-item label="模拟持仓">
             <el-input-number v-model="targetPos" :step="1" :min="1" @change="loadAnalysisData" style="width: 120px;" />
             <span style="margin-left: 5px">MW</span>
          </el-form-item>

          <el-divider direction="vertical" />

          <el-form-item label="显示滑点成本">
            <el-switch v-model="showSlippage" @change="renderChart" />
          </el-form-item>
        </el-form>

        <el-row :gutter="20" style="margin-top: 10px; padding-left: 10px;">
          <el-col :span="8">
             <span class="slider-label">PH 警戒线 ({{ thresholdPH }} MW)</span>
             <el-slider v-model="thresholdPH" :max="100" size="small" @input="updateChartVisuals" />
          </el-col>
          <el-col :span="8">
             <span class="slider-label">QH 警戒线 ({{ thresholdQH }} MW)</span>
             <el-slider v-model="thresholdQH" :max="50" size="small" @input="updateChartVisuals" />
          </el-col>
        </el-row>
      </div>

      <div v-loading="loading" class="chart-wrapper">
        <div ref="chartRef" style="width: 100%; height: 550px;"></div>
      </div>
      
      <div class="stats-footer" v-if="stats">
        <el-descriptions border :column="4">
          <el-descriptions-item label="PH 低流动性时段">
            <span style="color: red; font-weight: bold">{{ stats.phRiskCount }}</span> / 24
          </el-descriptions-item>
          <el-descriptions-item label="QH 低流动性时段">
            <span style="color: red; font-weight: bold">{{ stats.qhRiskCount }}</span> / 96
          </el-descriptions-item>
          <el-descriptions-item label="最高预估滑点">
            {{ stats.maxSlippage }} EUR/MWh
          </el-descriptions-item>
          <el-descriptions-item label="平均价格波动">
             {{ stats.avgVolatility }} EUR
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue';
import * as echarts from 'echarts';
import { getAnalysis, triggerFetch } from '../api/service';
import { ElMessage } from 'element-plus';

// --- 状态变量 ---
const currentArea = ref('SE3');
const queryDate = ref('2025-12-01'); 
const targetPos = ref(5.0);
const thresholdPH = ref(40);
const thresholdQH = ref(10);
const showSlippage = ref(true); // 默认显示滑点

const loading = ref(false);
const fetching = ref(false);
const chartRef = ref(null);
let myChart = null;

const rawData = ref({ ph: [], qh: [] });
const stats = ref({ phRiskCount: 0, qhRiskCount: 0, maxSlippage: 0, avgVolatility: 0 });

// --- 核心方法：加载数据 ---
const loadAnalysisData = async () => {
  if (!queryDate.value) return;
  
  loading.value = true;
  try {
    const res = await getAnalysis({
      start_date: queryDate.value,
      end_date: queryDate.value,
      area: currentArea.value,
      target_pos: targetPos.value
    });
    
    if (res.data.status === 'success') {
      rawData.value = res.data.data;
      calculateStats();
      renderChart();
    }
  } catch (error) {
    ElMessage.error('获取分析数据失败: ' + error.message);
  } finally {
    loading.value = false;
  }
};

const calculateStats = () => {
  const ph = rawData.value.ph;
  const qh = rawData.value.qh;
  
  // 1. 风险计数
  stats.value.phRiskCount = ph.filter(i => i.total_vol < thresholdPH.value).length;
  stats.value.qhRiskCount = qh.filter(i => i.total_vol < thresholdQH.value).length;
  
  // 2. 滑点与波动率统计 (合并 PH 和 QH)
  const allData = [...ph, ...qh];
  if (allData.length > 0) {
    const maxSlip = Math.max(...allData.map(i => i.est_slippage || 0));
    const avgVol = allData.reduce((sum, i) => sum + (i.std_price || 0), 0) / allData.length;
    
    stats.value.maxSlippage = maxSlip.toFixed(2);
    stats.value.avgVolatility = avgVol.toFixed(2);
  }
};

// --- 核心方法：渲染图表 ---
const renderChart = () => {
  if (!myChart) myChart = echarts.init(chartRef.value);
  if (!rawData.value.qh.length) return;

  // 准备 Series
  const series = [
    // 1. PH 阶梯线 (左轴)
    {
      name: 'PH 成交量',
      type: 'line',
      step: 'end',
      data: rawData.value.ph.map(i => [i.time_str.split(' ')[1], i.total_vol]),
      symbol: 'none',
      itemStyle: { color: '#1890ff' },
      lineStyle: { width: 3 },
      areaStyle: { opacity: 0.1 },
      yAxisIndex: 0
    },
    // 2. QH 柱状图 (左轴)
    {
      name: 'QH 成交量',
      type: 'bar',
      data: rawData.value.qh.map(i => ({
        value: [i.time_str.split(' ')[1], i.total_vol],
        itemStyle: {
          color: i.total_vol < thresholdQH.value ? '#ff4d4f' : 'rgba(250, 173, 20, 0.6)'
        }
      })),
      barWidth: '60%',
      yAxisIndex: 0
    },
    // 3. 阈值线
    {
      type: 'line',
      markLine: {
        symbol: 'none',
        data: [
          { yAxis: thresholdPH.value, name: 'PH限', lineStyle: { color: 'blue', type: 'dashed' } },
          { yAxis: thresholdQH.value, name: 'QH限', lineStyle: { color: 'orange', type: 'dashed' } }
        ]
      }
    }
  ];

  // 4. (可选) 滑点折线 (右轴)
  if (showSlippage.value) {
    // 为了防止滑点线过于密集，我们只画 QH 的滑点（因为它更敏感）
    series.push({
      name: '预估滑点 (EUR)',
      type: 'line',
      smooth: true,
      yAxisIndex: 1, // 关键：使用右侧Y轴
      data: rawData.value.qh.map(i => [i.time_str.split(' ')[1], i.est_slippage]),
      symbol: 'circle',
      symbolSize: 2,
      lineStyle: { color: '#f5222d', width: 1.5, type: 'dashed' },
      itemStyle: { color: '#f5222d' }
    });
  }

  const option = {
    title: { text: '' },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params) => {
        let html = `<b>${params[0].axisValue}</b><br/>`;
        params.forEach(p => {
          const val = p.value[1] !== undefined ? p.value[1] : p.value; // 兼容 bar 和 line 数据结构
          let unit = 'MW';
          let label = p.seriesName;
          
          if (p.seriesName.includes('滑点')) unit = 'EUR';
          
          html += `${p.marker} ${label}: <b>${val} ${unit}</b><br/>`;
        });
        return html;
      }
    },
    legend: {
      bottom: 0,
      data: [
        {
          name: 'PH 成交量',
          itemStyle: { color: '#1890ff' }
        },
        {
          name: 'QH 成交量',
          itemStyle: { color: 'rgba(250, 173, 20, 0.6)' }
        },
        {
          name: '预估滑点 (EUR)',
          itemStyle: { color: '#f5222d' }
        }
      ]
    },
    grid: { left: '3%', right: '3%', bottom: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: rawData.value.qh.map(i => i.time_str.split(' ')[1]),
      boundaryGap: true
    },
    // --- 双 Y 轴配置 ---
    yAxis: [
      {
        type: 'value',
        name: '成交量 (MW)',
        position: 'left',
        splitLine: { show: true, lineStyle: { type: 'dashed' } }
      },
      {
        type: 'value',
        name: '预估滑点 (EUR)',
        position: 'right',
        min: 0,
        // 动态计算最大值，稍微留点余地，不然线条会顶格
        // max: (val) => Math.ceil(val.max * 1.2), 
        splitLine: { show: false }, // 右轴不显示网格线，防止太乱
        axisLabel: { formatter: '{value} €' },
        axisLine: { show: true, lineStyle: { color: '#f5222d' } }
      }
    ],
    series: series
  };

  myChart.setOption(option, true); // true = 不合并，彻底重绘
};

const updateChartVisuals = () => {
  if(rawData.value.ph.length > 0) {
    calculateStats();
    renderChart();
  }
};

const handleFetchData = async () => {
  fetching.value = true;
  try {
    await triggerFetch({
      start_time: `${queryDate.value}T00:00:00Z`,
      end_time: `${queryDate.value}T23:59:59Z`,
      areas: [currentArea.value]
    });
    ElMessage.success('同步任务已启动');
  } catch (error) {
    ElMessage.error('触发失败');
  } finally {
    fetching.value = false;
  }
};

onMounted(() => {
  loadAnalysisData();
  window.addEventListener('resize', () => myChart && myChart.resize());
});
</script>

<style scoped>
.dashboard-container {
  padding-bottom: 20px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.control-panel {
  background: #f9fafc;
  padding: 15px;
  border-radius: 6px;
  margin-bottom: 20px;
}
.slider-label {
  font-size: 12px;
  color: #606266;
  display: block;
  margin-bottom: 5px;
}
.stats-footer {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid #ebeef5;
}
</style>