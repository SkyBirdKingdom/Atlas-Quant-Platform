<template>
  <div class="sim-layout">
    <div class="sim-sidebar">
      <el-card class="config-card">
        <template #header>
          <div class="card-header">
            <span>⚡ 策略配置</span>
            <el-button type="primary" size="small" @click="runSimulation" :loading="loading">运行</el-button>
          </div>
        </template>
        
        <el-form size="small" label-position="top">
          <el-form-item label="区域 & 时间">
            <el-select v-model="form.area" style="width: 100px; margin-right: 5px;">
              <el-option label="SE3" value="SE3" />
              <el-option label="SE4" value="SE4" />
            </el-select>
            <el-date-picker
              v-model="form.dateRange"
              type="daterange"
              style="width: 200px;"
              value-format="YYYY-MM-DD"
              start-placeholder="Start"
              end-placeholder="End"
            />
          </el-form-item>
        </el-form>

        <div class="json-box">
          <div class="json-header">strategy_config.json</div>
          <el-input
            v-model="form.configJson"
            type="textarea"
            :rows="15"
            class="code-font"
            placeholder="Paste config here..."
          />
        </div>
      </el-card>

      <el-card class="history-card">
        <template #header>
          <div class="card-header">
            <span>📜 模拟历史 (Session)</span>
            <el-button type="text" @click="history = []">清空</el-button>
          </div>
        </template>
        <div class="history-list">
          <div 
            v-for="(item, index) in history" 
            :key="index"
            class="history-item"
            :class="{ active: currentResult === item, selected: compareList.includes(item) }"
            @click="viewResult(item)"
          >
            <div class="h-top">
              <span class="h-time">{{ formatTime(item.timestamp) }}</span>
              <el-checkbox 
                v-model="compareList" 
                :label="item" 
                @click.stop
                :disabled="compareList.length >= 2 && !compareList.includes(item)"
              >对比</el-checkbox>
            </div>
            <div class="h-stat" :class="item.data.summary.total_pnl >= 0 ? 'text-green' : 'text-red'">
              {{ item.data.summary.total_pnl.toFixed(2) }}€
            </div>
          </div>
        </div>
        <el-button 
          v-if="compareList.length === 2" 
          class="compare-btn" 
          type="warning" 
          @click="showComparison = true"
        >
          开始对比 ({{ compareList.length }}/2)
        </el-button>
      </el-card>
    </div>

    <div class="sim-content">
      <div v-if="!currentResult" class="empty-state">
        <el-empty description="配置策略并点击运行以查看结果" />
      </div>

      <div v-else class="result-dashboard">
        <el-row :gutter="15" class="metrics-row">
          <el-col :span="6">
            <el-card shadow="hover" class="metric-card">
              <div class="label">总盈亏 (Total PnL)</div>
              <div class="value" :class="getPnlClass(currentResult.data.summary.total_pnl)">
                {{ currentResult.data.summary.total_pnl.toFixed(2) }} €
              </div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="hover" class="metric-card">
              <div class="label">夏普比率 (Sharpe)</div>
              <div class="value">{{ currentResult.data.summary.sharpe_ratio.toFixed(2) }}</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="hover" class="metric-card">
              <div class="label">最大回撤 (Max Drawdown)</div>
              <div class="value text-red">{{ (currentResult.data.summary.max_drawdown * 100).toFixed(2) }}%</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="hover" class="metric-card">
              <div class="label">交易次数 (Trades)</div>
              <div class="value">{{ currentResult.data.summary.trade_count }}</div>
            </el-card>
          </el-col>
        </el-row>

        <el-card class="chart-card">
          <v-chart class="chart" :option="chartOption" autoresize />
        </el-card>

        <el-card class="table-card">
          <el-tabs>
            <el-tab-pane label="合约统计">
              <el-table :data="currentResult.data.contracts" height="300" stripe>
                <el-table-column prop="contract_id" label="合约" />
                <el-table-column prop="pnl" label="盈亏" sortable>
                  <template #default="scope">
                    <span :class="getPnlClass(scope.row.pnl)">{{ scope.row.pnl.toFixed(2) }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="trade_count" label="成交数" sortable />
                <el-table-column prop="slippage" label="滑点" />
                <el-table-column prop="fees" label="手续费" />
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </div>
    </div>

    <el-dialog v-model="showComparison" title="策略版本对比" width="90%" fullscreen>
      <div v-if="compareList.length === 2" class="compare-view">
        <el-row :gutter="20">
          <el-col :span="16">
            <el-card title="权益曲线对比">
              <v-chart class="chart-compare" :option="compareChartOption" autoresize />
            </el-card>
            
            <el-table :data="compareMetrics" style="margin-top: 20px" border>
              <el-table-column prop="metric" label="指标" />
              <el-table-column :label="'版本 A (' + formatTime(compareList[0].timestamp) + ')'" align="center">
                <template #default="scope">
                  <span :class="getDiffClass(scope.row.diff)">{{ scope.row.val1 }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="'版本 B (' + formatTime(compareList[1].timestamp) + ')'" align="center">
                <template #default="scope">
                   <span :class="getDiffClass(-scope.row.diff)">{{ scope.row.val2 }}</span>
                </template>
              </el-table-column>
              <el-table-column label="差异" align="center">
                 <template #default="scope">
                   {{ scope.row.diffStr }}
                </template>
              </el-table-column>
            </el-table>
          </el-col>
          
          <el-col :span="8">
            <el-card class="diff-card">
              <template #header>配置差异 (Config Diff)</template>
              <div class="json-diff-view">
                 <div v-html="configDiffHtml"></div>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from 'echarts/components'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent, TitleComponent])

// 状态
const loading = ref(false)
const history = ref([]) // 历史运行记录
const currentResult = ref(null) // 当前选中的结果
const compareList = ref([]) // 选中的对比项
const showComparison = ref(false)

const form = reactive({
  area: 'SE3',
  dateRange: ['2025-01-01', '2025-01-07'],
  initial_capital: 50000,
  configJson: JSON.stringify({
    "strategy_params": {
      "delivery_time_buy": { "position_ratio": 1.0, "position_split": 1 },
      "super_mean_reversion_buy": { "position_ratio": 0.5, "threshold": -0.05 },
      "optimized_extreme_sell": { "position_ratio": 0.5, "z_score_threshold": 4.0 }
    }
  }, null, 2)
})

// --- 核心逻辑 ---

const runSimulation = async () => {
  try {
    const configObj = JSON.parse(form.configJson)
    loading.value = true
    
    // 模拟 API 调用
    const resp = await axios.post('http://localhost:8000/api/simulation/legacy-run', null, {
      params: { area: form.area, start_date: form.dateRange[0], end_date: form.dateRange[1] },
      data: { ...configObj, initial_capital: form.initial_capital }
    })
    
    if (resp.data.status === 'success') {
      const resultItem = {
        id: Date.now(),
        timestamp: new Date(),
        config: configObj, // 保存当时的配置
        data: resp.data.data
      }
      history.value.unshift(resultItem) // 加到历史记录顶部
      currentResult.value = resultItem
      ElMessage.success('模拟完成')
    }
  } catch (e) {
    ElMessage.error('Error: ' + e.message)
  } finally {
    loading.value = false
  }
}

const viewResult = (item) => {
  currentResult.value = item
}

// --- 图表配置 (ECharts) ---

const chartOption = computed(() => {
  if (!currentResult.value) return {}
  const curve = currentResult.value.data.equity_curve
  return {
    tooltip: { trigger: 'axis' },
    grid: { top: 30, right: 30, bottom: 30, left: 60 },
    xAxis: { type: 'category', data: curve.map(i => i.time) },
    yAxis: { type: 'value', scale: true },
    series: [{
      data: curve.map(i => i.value),
      type: 'line',
      smooth: true,
      areaStyle: { opacity: 0.1 },
      itemStyle: { color: '#409EFF' }
    }]
  }
})

// --- 对比逻辑 ---

const compareChartOption = computed(() => {
  if (compareList.value.length !== 2) return {}
  const [resA, resB] = compareList.value
  
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['版本 A', '版本 B'] },
    xAxis: { type: 'category', data: resA.data.equity_curve.map(i => i.time) }, // 假设时间轴一致
    yAxis: { type: 'value', scale: true },
    series: [
      { name: '版本 A', type: 'line', data: resA.data.equity_curve.map(i => i.value), smooth: true },
      { name: '版本 B', type: 'line', data: resB.data.equity_curve.map(i => i.value), smooth: true }
    ]
  }
})

const compareMetrics = computed(() => {
  if (compareList.value.length !== 2) return []
  const [a, b] = compareList.value
  const sA = a.data.summary
  const sB = b.data.summary
  
  return [
    { metric: '总盈亏 (€)', val1: sA.total_pnl.toFixed(2), val2: sB.total_pnl.toFixed(2), diff: sB.total_pnl - sA.total_pnl, diffStr: (sB.total_pnl - sA.total_pnl).toFixed(2) },
    { metric: '夏普比率', val1: sA.sharpe_ratio.toFixed(2), val2: sB.sharpe_ratio.toFixed(2), diff: sB.sharpe_ratio - sA.sharpe_ratio, diffStr: (sB.sharpe_ratio - sA.sharpe_ratio).toFixed(2) },
    { metric: '交易次数', val1: sA.trade_count, val2: sB.trade_count, diff: sB.trade_count - sA.trade_count, diffStr: sB.trade_count - sA.trade_count }
  ]
})

// 简易 JSON Diff 高亮 (实际项目建议使用专门的 diff 库)
const configDiffHtml = computed(() => {
  if (compareList.value.length !== 2) return ''
  const confA = JSON.stringify(compareList.value[0].config, null, 2).split('\n')
  const confB = JSON.stringify(compareList.value[1].config, null, 2).split('\n')
  
  let html = '<pre style="font-size: 12px; line-height: 1.5;">'
  // 极简对比行数
  const maxLen = Math.max(confA.length, confB.length)
  for (let i = 0; i < maxLen; i++) {
    const lineA = confA[i] || ''
    const lineB = confB[i] || ''
    if (lineA !== lineB) {
      html += `<div style="background: #fdf6ec; color: #e6a23c;">${lineB}  <span style="color: #999">// Changed</span></div>`
    } else {
      html += `<div>${lineB}</div>`
    }
  }
  html += '</pre>'
  return html
})

// 辅助函数
const formatTime = (date) => {
  return new Date(date).toLocaleTimeString()
}
const getPnlClass = (val) => val >= 0 ? 'text-green' : 'text-red'
const getDiffClass = (diff) => diff > 0 ? 'text-green' : (diff < 0 ? 'text-red' : '')

</script>

<style scoped>
.sim-layout { display: flex; height: 100vh; background: #f0f2f5; }
.sim-sidebar { width: 350px; padding: 10px; display: flex; flex-direction: column; gap: 10px; border-right: 1px solid #ddd; background: white; }
.sim-content { flex: 1; padding: 20px; overflow-y: auto; }

.history-list { max-height: 300px; overflow-y: auto; }
.history-item { padding: 10px; border-bottom: 1px solid #eee; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
.history-item:hover { background: #f5f7fa; }
.history-item.active { background: #ecf5ff; border-left: 3px solid #409EFF; }

.metrics-row { margin-bottom: 20px; }
.metric-card .value { font-size: 24px; font-weight: bold; margin-top: 10px; }
.text-green { color: #67C23A; }
.text-red { color: #F56C6C; }

.chart { height: 350px; width: 100%; }
.chart-compare { height: 400px; width: 100%; }

.code-font { font-family: 'Consolas', monospace; font-size: 12px; }
</style>