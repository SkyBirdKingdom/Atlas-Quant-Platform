<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <div class="header-title">
          <el-icon><Compass /></el-icon>
          <span>参数矩阵扫描 (Grid Search)</span>
        </div>
      </div>
    </template>

    <div class="opt-container">
      <div class="config-col">
        <el-form label-position="top" size="small">
          <el-alert title="寻找策略的舒适区" type="info" :closable="false" style="margin-bottom:15px" show-icon />
          
          <el-form-item label="基础设置">
             <div style="display:flex; gap:10px; margin-bottom: 5px;">
                <el-radio-group v-model="form.area" size="small">
                  <el-radio-button label="SE3" /><el-radio-button label="SE4" />
                </el-radio-group>
                <el-date-picker v-model="form.range" type="daterange" style="flex:1" value-format="YYYY-MM-DD" />
             </div>
          </el-form-item>

          <el-divider content-position="left">待优化参数 (X轴 / Y轴)</el-divider>
          
          <el-form-item label="RSI 买入阈值 (rsi_buy)">
             <div class="range-input">
                <el-input-number v-model="grid.rsi_buy.start" :step="5" controls-position="right" />
                <span>-</span>
                <el-input-number v-model="grid.rsi_buy.end" :step="5" controls-position="right" />
                <span>步长:</span>
                <el-input-number v-model="grid.rsi_buy.step" :min="1" :step="1" style="width: 70px" />
             </div>
             <div class="preview-tags">
                <el-tag size="small" v-for="v in generateRange('rsi_buy')" :key="v">{{ v }}</el-tag>
             </div>
          </el-form-item>

          <el-form-item label="RSI 卖出阈值 (rsi_sell)">
             <div class="range-input">
                <el-input-number v-model="grid.rsi_sell.start" :step="5" controls-position="right" />
                <span>-</span>
                <el-input-number v-model="grid.rsi_sell.end" :step="5" controls-position="right" />
                <span>步长:</span>
                <el-input-number v-model="grid.rsi_sell.step" :min="1" style="width: 70px" />
             </div>
             <div class="preview-tags">
                <el-tag size="small" type="warning" v-for="v in generateRange('rsi_sell')" :key="v">{{ v }}</el-tag>
             </div>
          </el-form-item>

          <el-button 
            type="primary" 
            size="large" 
            style="width:100%; margin-top:20px" 
            @click="runOptimization" 
            :loading="loading"
          >
             {{ loading ? progressText : `🚀 开始扫描 (${totalCombinations} 组)` }}
          </el-button>
        </el-form>
      </div>

      <div class="chart-col">
        <div v-if="!hasResult" class="empty-state">
           <el-empty description="请配置左侧参数并开始运行" />
        </div>
        <div v-else>
           <div class="best-param-bar">
              🏆 最佳组合: 
              <span class="highlight">Buy={{ bestResult.params.rsi_buy }}</span> / 
              <span class="highlight">Sell={{ bestResult.params.rsi_sell }}</span> 
              ➡️ 净利润: <span class="profit">{{ bestResult.pnl }} €</span>
              <span style="margin-left:15px; font-size:12px; color:#666">
                (胜率: {{ bestResult.win_rate }}% | 交易: {{ bestResult.trades }})
              </span>
           </div>
           <div ref="heatmapRef" style="width: 100%; height: 500px;"></div>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onMounted, onUnmounted } from 'vue';
import * as echarts from 'echarts';
import { Compass } from '@element-plus/icons-vue';
import { getBacktestOptimize, getOptimizationStatus } from '../api/service'; // 引入新 API
import { ElMessage } from 'element-plus';

const loading = ref(false);
const progressText = ref('提交中...');
const hasResult = ref(false);
const heatmapRef = ref(null);
let myChart = null;
const bestResult = ref({});

const form = reactive({
  area: 'SE3',
  range: ['2025-12-01', '2025-12-05'],
  base_rules: {
      buy: [{ indicator: 'RSI_14', op: '<', val: 30 }], 
      sell: [{ indicator: 'RSI_14', op: '>', val: 70 }]
  }
});

const grid = reactive({
  rsi_buy: { start: 20, end: 40, step: 5 },
  rsi_sell: { start: 60, end: 80, step: 5 }
});

const generateRange = (key) => {
  const { start, end, step } = grid[key];
  const res = [];
  for (let i = start; i <= end; i += step) {
    res.push(i);
  }
  return res;
};

const totalCombinations = computed(() => {
  return generateRange('rsi_buy').length * generateRange('rsi_sell').length;
});

const runOptimization = async () => {
  loading.value = true;
  hasResult.value = false;
  progressText.value = '提交中...';
  
  const paramGrid = {
      rsi_buy: generateRange('rsi_buy'),
      rsi_sell: generateRange('rsi_sell')
  };
  
  try {
    // 1. 提交任务
    const res = await getBacktestOptimize({
        area: form.area,
        start_date: form.range[0],
        end_date: form.range[1],
        base_params: { max_pos: 5.0, enable_slippage: true },
        rules: form.base_rules,
        param_grid: paramGrid
    });
    
    if (res.data.status === 'success') {
        const taskId = res.data.task_id;
        progressText.value = '计算中...';
        
        // 2. 开始轮询
        const poll = setInterval(async () => {
            try {
                const statusRes = await getOptimizationStatus(taskId);
                const state = statusRes.data;
                
                if (state.status === 'completed') {
                    // === 成功结束 ===
                    clearInterval(poll);
                    loading.value = false; // 只有在这里才关闭 Loading！
                    
                    const resultList = state.data.results;
                    if (resultList && resultList.length > 0) {
                        hasResult.value = true;
                        bestResult.value = resultList[0]; // 已排序
                        await nextTick();
                        renderHeatmap(resultList, paramGrid);
                        ElMessage.success(`扫描完成，共测试 ${resultList.length} 组参数`);
                    } else {
                        ElMessage.warning('没有产生有效结果');
                    }
                    
                } else if (state.status === 'failed') {
                    // === 失败结束 ===
                    clearInterval(poll);
                    loading.value = false;
                    ElMessage.error('优化失败: ' + state.message);
                } else {
                    // === 继续跑 ===
                    // 可以根据 progress 更新 progressText
                    progressText.value = '疯狂计算中...';
                }
            } catch (err) {
                console.error(err);
                // 网络错误不一定代表任务失败，可以重试，这里简单处理为中断
                // clearInterval(poll);
                // loading.value = false;
            }
        }, 1000); // 每秒轮询一次
        
    } else {
        loading.value = false;
        ElMessage.error(res.data.msg);
    }
  } catch (e) {
    loading.value = false;
    ElMessage.error('请求失败: ' + e.message);
  }
};

const renderHeatmap = (results, paramGrid) => {
  if (!heatmapRef.value) return;
  if (myChart) myChart.dispose();
  myChart = echarts.init(heatmapRef.value);
  
  const xData = paramGrid.rsi_buy;
  const yData = paramGrid.rsi_sell;
  
  // 转换数据为 [x, y, value]
  const data = results.map(item => {
      const xIdx = xData.indexOf(item.params.rsi_buy);
      const yIdx = yData.indexOf(item.params.rsi_sell);
      // xIdx, yIdx 可能为 -1 (如果参数不在 Grid 里)，需注意
      return [xIdx, yIdx, item.pnl];
  });
  
  const minVal = Math.min(...data.map(d => d[2]));
  const maxVal = Math.max(...data.map(d => d[2]));

  const option = {
    tooltip: {
      position: 'top',
      formatter: (p) => {
          return `Buy: ${xData[p.value[0]]}<br>Sell: ${yData[p.value[1]]}<br>PnL: <b>${p.value[2]} €</b>`;
      }
    },
    grid: { height: '80%', top: '10%', bottom: '15%' },
    xAxis: {
      type: 'category',
      data: xData,
      name: 'RSI Buy',
      nameLocation: 'middle',
      nameGap: 30,
      splitArea: { show: true }
    },
    yAxis: {
      type: 'category',
      data: yData,
      name: 'RSI Sell',
      nameLocation: 'middle',
      nameGap: 30,
      splitArea: { show: true }
    },
    visualMap: {
      min: minVal,
      max: maxVal,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      inRange: {
          color: ['#f56c6c', '#e6a23c', '#e1f3d8', '#67c23a']
      }
    },
    series: [{
      type: 'heatmap',
      data: data,
      label: { show: true },
      itemStyle: {
        emphasis: { shadowBlur: 10, shadowColor: 'rgba(0, 0, 0, 0.5)' }
      }
    }]
  };
  
  myChart.setOption(option);
};

onMounted(() => window.addEventListener('resize', () => myChart && myChart.resize()));
</script>

<style scoped>
.opt-container { display: flex; gap: 20px; height: 700px; }
.config-col { width: 320px; background: #f8f9fa; padding: 20px; border-radius: 8px; flex-shrink: 0; }
.chart-col { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.range-input { display: flex; align-items: center; gap: 5px; }
.preview-tags { margin-top: 5px; display: flex; flex-wrap: wrap; gap: 5px; }
.empty-state { display: flex; justify-content: center; align-items: center; height: 100%; background: #fefefe; border: 2px dashed #eee; border-radius: 8px; }
.best-param-bar { background: #f0f9eb; color: #67c23a; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-size: 16px; border: 1px solid #c2e7b0; }
.highlight { font-weight: bold; color: #303133; margin: 0 5px; }
.profit { font-weight: bold; font-size: 20px; }
</style>