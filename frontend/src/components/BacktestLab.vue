<template>
  <el-card class="lab-card">
    <template #header>
      <div class="card-header">
        <div class="header-title">
          <el-icon><TrendCharts /></el-icon>
          <span>智能策略实验室 (Smart Strategy Lab)</span>
        </div>
      </div>
    </template>

    <div class="lab-container">
      <div class="config-panel">
        <el-scrollbar>
          <el-form label-position="top" size="small">
            <el-divider content-position="left">基础设置</el-divider>
            <el-form-item label="交易区域">
              <div style="display: flex; gap: 5px;">
                <el-radio-group v-model="form.area" size="small" style="flex:1; display: flex; gap: 10px;">
                  <el-radio-button label="SE1" />
                  <el-radio-button label="SE2" />
                  <el-radio-button label="SE3" />
                  <el-radio-button label="SE4" />
                </el-radio-group>
              </div>
            </el-form-item>
            
            <el-form-item label="回测区间">
              <el-date-picker v-model="form.range" type="daterange" style="flex:1" range-separator="-" start-placeholder="Start" end-placeholder="End" value-format="YYYY-MM-DD" />
            </el-form-item>
            
            <el-form-item label="风控参数">
              <el-row :gutter="10">
                <el-col :span="12">
                  <div class="sub-label">单合约最大持仓</div>
                  <el-input-number v-model="form.params.max_pos" :min="1" style="width: 100%" />
                </el-col>
                <el-col :span="12">
                  <div class="sub-label">收盘前强平(分)</div>
                  <el-input-number v-model="form.params.force_close_minutes" :min="0" :max="60" style="width: 100%" />
                </el-col>
              </el-row>
            </el-form-item>

            <el-form-item label="成本设置">
              <el-checkbox v-model="form.params.enable_slippage" label="启用滑点/冲击成本计算" border />
            </el-form-item>

            <el-divider content-position="left">
              <span style="color: #67c23a"><el-icon><Top /></el-icon> 买入/做多规则 (AND)</span>
            </el-divider>
            
            <div v-for="(rule, idx) in form.rules.buy" :key="'b'+idx" class="rule-row">
              <el-select v-model="rule.indicator" style="width: 110px" placeholder="指标">
                <el-option-group label="价格与趋势">
                   <el-option label="收盘价" value="close" />
                   <el-option label="SMA 50" value="SMA_50" />
                   <el-option label="SMA 200" value="SMA_200" />
                   <el-option label="布林下轨" value="BBL_20_2.0" />
                   <el-option label="布林上轨" value="BBU_20_2.0" />
                </el-option-group>
                <el-option-group label="震荡指标">
                   <el-option label="RSI (14)" value="RSI_14" />
                   <el-option label="CCI (20)" value="CCI_20_0.015" />
                   <el-option label="MACD柱" value="MACDh_12_26_9" />
                </el-option-group>
              </el-select>
              <el-select v-model="rule.op" style="width: 60px">
                <el-option label="<" value="<" /><el-option label=">" value=">" />
              </el-select>
              <el-input v-model="rule.val" style="width: 90px" placeholder="值或指标" />
              <el-button type="danger" icon="Delete" circle size="small" @click="removeRule('buy', idx)" />
            </div>
            <el-button type="primary" link icon="Plus" size="small" @click="addRule('buy')">添加买入条件</el-button>

            <el-divider content-position="left">
              <span style="color: #f56c6c"><el-icon><Bottom /></el-icon> 卖出/做空规则 (AND)</span>
            </el-divider>

            <div v-for="(rule, idx) in form.rules.sell" :key="'s'+idx" class="rule-row">
              <el-select v-model="rule.indicator" style="width: 110px" placeholder="指标">
                <el-option-group label="价格与趋势">
                   <el-option label="收盘价" value="close" />
                   <el-option label="SMA 50" value="SMA_50" />
                   <el-option label="SMA 200" value="SMA_200" />
                   <el-option label="布林下轨" value="BBL_20_2.0" />
                   <el-option label="布林上轨" value="BBU_20_2.0" />
                </el-option-group>
                <el-option-group label="震荡指标">
                   <el-option label="RSI (14)" value="RSI_14" />
                   <el-option label="CCI (20)" value="CCI_20_0.015" />
                   <el-option label="MACD柱" value="MACDh_12_26_9" />
                </el-option-group>
              </el-select>
              <el-select v-model="rule.op" style="width: 60px">
                <el-option label="<" value="<" /><el-option label=">" value=">" />
              </el-select>
              <el-input v-model="rule.val" style="width: 90px" placeholder="值或指标" />
              <el-button type="danger" icon="Delete" circle size="small" @click="removeRule('sell', idx)" />
            </div>
            <el-button type="danger" link icon="Plus" size="small" @click="addRule('sell')">添加卖出条件</el-button>

            <div style="margin-top: 20px">
              <el-button type="primary" size="large" style="width: 100%" @click="runTest" :loading="loading">
                🚀 执行回测
              </el-button>
            </div>
          </el-form>
        </el-scrollbar>
      </div>

      <div class="result-panel">
        <div v-if="summary" class="summary-box">
          <div class="stat-item main-stat">
            <div class="stat-label">累计净利润 (Total PnL)</div>
            <div class="stat-value huge" :class="summary.total_pnl >= 0 ? 'text-up' : 'text-down'">
              {{ summary.total_pnl }} <span class="unit">€</span>
            </div>
          </div>

          <div class="stat-divider"></div>

          <div class="stat-item">
             <div class="stat-label">
               盈亏比 (Profit Factor)
               <el-tooltip content="总盈利 / |总亏损|。>1.5 为优秀，>2.0 为极好。" placement="top">
                 <el-icon class="icon-help"><InfoFilled /></el-icon>
               </el-tooltip>
             </div>
             <div class="stat-value" :class="getPFColor(summary.profit_factor)">
               {{ summary.profit_factor }}
             </div>
          </div>

          <div class="stat-divider"></div>

          <div class="stat-item">
             <div class="stat-label">
               夏普比率 (Sharpe)
               <el-tooltip content="承受单位风险获得的超额回报。>1.0 代表策略稳健。" placement="top">
                 <el-icon class="icon-help"><InfoFilled /></el-icon>
               </el-tooltip>
             </div>
             <div class="stat-value" :class="summary.sharpe_ratio > 1 ? 'text-up' : ''">
               {{ summary.sharpe_ratio }}
             </div>
          </div>
          
          <div class="stat-divider"></div>

          <div class="stat-group">
             <div class="sub-stat">
               <span class="sub-label">最大回撤:</span>
               <span class="sub-value text-down">{{ summary.max_drawdown }} €</span>
             </div>
             <div class="sub-stat">
               <span class="sub-label">合约胜率:</span>
               <span class="sub-value" :class="summary.win_rate > 50 ? 'text-up' : 'text-down'">{{ summary.win_rate }}%</span>
             </div>
             <div class="sub-stat">
               <span class="sub-label">交易笔数:</span>
               <span class="sub-value">{{ summary.trade_count }}</span>
             </div>
          </div>
        </div>

        <el-table :data="contractList" height="100%" style="width: 100%; margin-top: 10px" stripe border size="small" @row-click="showDetail">
          <el-table-column prop="contract_id" label="合约 ID" width="130" fixed sortable />
          <el-table-column label="交割时段" width="160">
             <template #default="scope">
                {{ scope.row.delivery_start.split(' ')[0] }} 
                <b>{{ scope.row.delivery_start.split(' ')[1] }}-{{ scope.row.delivery_end }}</b>
             </template>
          </el-table-column>
          <el-table-column prop="pnl" label="净盈亏 (PnL)" sortable width="120">
             <template #default="scope">
                <el-tag :type="scope.row.pnl >= 0 ? 'success' : 'danger'" effect="plain">
                   {{ formatNum(scope.row.pnl) }} €
                </el-tag>
             </template>
          </el-table-column>
          <el-table-column prop="trade_count" label="交易数" width="80" sortable />
          <el-table-column prop="slippage" label="成本" width="80">
             <template #default="scope">{{ formatNum(scope.row.slippage) }}</template>
          </el-table-column>
          <el-table-column label="操作" min-width="80" align="center">
             <template #default><el-button link type="primary" icon="DataAnalysis">复盘</el-button></template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <el-dialog v-model="detailVisible" :title="`合约复盘: ${currentContract.contract_id}`" width="900px" destroy-on-close top="5vh">
      <div class="chart-meta">
         <el-tag type="info">开盘: {{ currentContract.open_time }}</el-tag>
         <el-icon><Right /></el-icon>
         <el-tag type="warning">收盘: {{ currentContract.close_time }}</el-tag>
         <el-icon><Right /></el-icon>
         <el-tag type="danger">交割: {{ currentContract.delivery_start }}</el-tag>
      </div>
      
      <div ref="chartRef" style="width: 100%; height: 350px;"></div>
      
      <el-table :data="currentContract.details" height="250" stripe border size="small" style="margin-top: 15px">
         <el-table-column prop="time" label="时间" width="140" />
         <el-table-column prop="action" label="动作" width="100">
            <template #default="scope">
               <span :style="{ color: getActionColor(scope.row.action), fontWeight: 'bold' }">{{ scope.row.action }}</span>
            </template>
         </el-table-column>
         <el-table-column prop="price" label="价格" />
         <el-table-column prop="vol" label="量 (MW)" />
         <el-table-column prop="signal" label="触发信号" show-overflow-tooltip />
         <el-table-column prop="cost" label="成本" />
      </el-table>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, nextTick, onMounted } from 'vue';
import * as echarts from 'echarts';
import { runBacktest, getBacktestStatus } from '../api/service';
import { ElMessage } from 'element-plus';
import { TrendCharts, DataAnalysis, Plus, Delete, Top, Bottom, Right, InfoFilled } from '@element-plus/icons-vue';

const loading = ref(false);
const summary = ref(null);
const contractList = ref([]);
const detailVisible = ref(false);
const currentContract = ref({});
const chartRef = ref(null);
let myChart = null;

const form = reactive({
  area: 'SE3', range: ['2025-12-01', '2025-12-01'],
  params: { max_pos: 2.0, force_close_minutes: 10, enable_slippage: false },
  // 新增：动态规则配置
  rules: {
    buy: [
      { indicator: 'RSI_14', op: '<', val: 30 },     // 1. 短期跌过头了
      // 这里的 val 需要填具体的数值，比较 close > SMA_50 这种跨指标比较
      // 我们目前的 DynamicConfigStrategy 还不支持 "指标 vs 指标"，只支持 "指标 vs 数值"
      // 为了先跑通，我们先只用 RSI 和 MACD 组合
      { indicator: 'MACDh_12_26_9', op: '>', val: 0 } // 2. 且 MACD 动能必须是红柱 (开始反弹)
    ],
    sell: [
      { indicator: 'RSI_14', op: '>', val: 70 }
    ]
  }
});

// 添加规则
const addRule = (type) => form.rules[type].push({ indicator: 'RSI_14', op: '<', val: 0 });
const removeRule = (type, idx) => form.rules[type].splice(idx, 1);

const formatNum = (v) => Number(v).toFixed(2);
const getActionColor = (a) => a === 'BUY' ? '#67c23a' : (a === 'SELL' ? '#f56c6c' : '#909399');

const getPFColor = (pf) => {
    const val = parseFloat(pf);
    if (val >= 2.0) return 'text-gold'; // 极好
    if (val >= 1.5) return 'text-up';   // 优秀
    if (val >= 1.0) return 'text-gray'; //及格
    return 'text-down'; // 亏损
};

const runTest = async () => {
  if (!form.range) return;
  loading.value = true;
  summary.value = null; contractList.value = [];
  
  try {
    // 构造参数：将 rules 合并进 params 传给后端
    const requestParams = {
        ...form.params,
        rules: form.rules
    };

    const res = await runBacktest({
      start_date: form.range[0], end_date: form.range[1], area: form.area,
      strategy_name: "DynamicConfig", // 指定使用通用策略
      params: requestParams
    });
    
    if (res.data.status === 'success') {
      const taskId = res.data.task_id;
      ElMessage.info('策略运算中...');
      
      const poll = setInterval(async () => {
        try {
            const statusRes = await getBacktestStatus(taskId);
            if (statusRes.data.status === 'completed') {
                clearInterval(poll);
                loading.value = false;
                summary.value = statusRes.data.data.summary;
                contractList.value = statusRes.data.data.contracts;
                ElMessage.success('回测完成');
            } else if (statusRes.data.status === 'failed') {
                clearInterval(poll);
                loading.value = false;
                ElMessage.error(statusRes.data.message);
            }
        } catch (e) { clearInterval(poll); loading.value = false; }
      }, 5000);
    } else { loading.value = false; ElMessage.error(res.data.msg); }
  } catch (e) { loading.value = false; }
};

const showDetail = async (row) => {
  currentContract.value = row;
  detailVisible.value = true;
  await nextTick();
  renderDetailChart(row);
};

const renderDetailChart = (contract) => {
  if (!chartRef.value) return;
  if (myChart) myChart.dispose();
  myChart = echarts.init(chartRef.value);
  
  const data = contract.chart;
  const times = data.map(d => d.t);
  const prices = data.map(d => d.p);
  const volumes = data.map(d => d.v);
  
  const markers = [];
  data.forEach((d, idx) => {
      if (d.a === 'BUY') markers.push({ name:'Buy', coord:[idx, d.p], itemStyle:{color:'#67c23a'}, value:'B' });
      if (d.a === 'SELL') markers.push({ name:'Sell', coord:[idx, d.p], itemStyle:{color:'#f56c6c'}, value:'S' });
      if (d.a === 'FORCE_CLOSE') markers.push({ name:'Force', coord:[idx, d.p], itemStyle:{color:'#7b1fa2'}, value:'Force' });
  });

  const markLines = [
      { name: 'Open', xAxis: 0, label: { formatter: 'Open', position: 'start' }, lineStyle: { color: 'green', type: 'dashed' } },
      { name: 'Close', xAxis: times.length - 1, label: { formatter: 'Close', position: 'end' }, lineStyle: { color: 'red', type: 'dashed' } }
  ];

  myChart.setOption({
      // 1. 启用动画效果，体验更丝滑
      animation: true,
      
      tooltip: { 
          trigger: 'axis',
          axisPointer: { type: 'cross' },
          // 优化 Tooltip 显示，让价格和成交量对齐
          formatter: function (params) {
              let res = `<div>${params[0].axisValue}</div>`;
              params.forEach(item => {
                  if (item.seriesName === '价格') {
                      res += `<div style="color:${item.color}">Price: ${item.value}</div>`;
                  } else if (item.seriesName === '成交量') {
                      res += `<div style="color:#999">Vol: ${item.value} MW</div>`;
                  }
              });
              return res;
          }
      },
      
      // 2. 坐标轴指示器同步
      axisPointer: { link: { xAxisIndex: 'all' } },
      
      legend: { data: ['价格', '成交量'] },
      
      // 3. 布局调整：留出底部空间给滚动条
      grid: [
          { left: 50, right: 30, top: 30, height: '55%' }, // 价格图高度
          { left: 50, right: 30, top: '70%', height: '20%' } // 成交量图高度 (中间留一点间隙)
      ],
      
      xAxis: [
          { 
            type: 'category', 
            data: times, 
            gridIndex: 0,
            boundaryGap: false, // 也就是 K 线那种紧凑风格
            axisLine: { onZero: false }
          },
          { 
            type: 'category', 
            data: times, 
            gridIndex: 1, 
            show: false // 隐藏第二个 X 轴的标签，但刻度保留用于对齐
          }
      ],
      
      yAxis: [
          { type: 'value', scale: true, name: '价格', gridIndex: 0, splitLine: { show: true } },
          { type: 'value', name: '量', gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } } // 隐藏成交量 Y 轴刻度，防止遮挡
      ],
      
      // 4. 【核心功能】缩放与滚动组件
      dataZoom: [
          {
              type: 'inside', // 支持鼠标滚轮缩放
              xAxisIndex: [0, 1], // 同时控制两个 X 轴
              start: 0,
              end: 100
          },
          {
              type: 'slider', // 底部显示滑动条
              xAxisIndex: [0, 1],
              top: '92%', // 放在最底部
              height: 20,
              start: 0,
              end: 100,
              handleIcon: 'path://M10.7,11.9v-1.3H9.3v1.3c-4.9,0.3-8.8,4.4-8.8,9.4c0,5,3.9,9.1,8.8,9.4v1.3h1.3v-1.3c4.9-0.3,8.8-4.4,8.8-9.4C19.5,16.3,15.6,12.2,10.7,11.9z M13.3,24.4H6.7V23h6.6V24.4z M13.3,19.6H6.7v-1.4h6.6V19.6z',
              handleSize: '80%',
              handleStyle: {
                  color: '#fff',
                  shadowBlur: 3,
                  shadowColor: 'rgba(0, 0, 0, 0.6)',
                  shadowOffsetX: 2,
                  shadowOffsetY: 2
              }
          }
      ],
      
      series: [
          {
              name: '价格', type: 'line', data: prices,
              xAxisIndex: 0, yAxisIndex: 0,
              markPoint: { data: markers, symbolSize: 40 },
              markLine: { symbol: 'none', data: markLines },
              lineStyle: { width: 2, color: '#409eff' },
              showSymbol: false // 鼠标不放上去时不显示小圆点
          },
          {
              name: '成交量', type: 'bar', data: volumes,
              xAxisIndex: 1, yAxisIndex: 1,
              itemStyle: { color: '#dfe6e9' }
          }
      ]
  });
};

onMounted(() => window.addEventListener('resize', () => myChart && myChart.resize()));
</script>

<style scoped>
.lab-container { display: flex; gap: 20px; height: 800px; }
.config-panel { width: 340px; background: #f8f9fa; padding: 15px; border-radius: 8px; height: 100%; overflow-y: auto; flex-shrink: 0; }
.result-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.rule-row { display: flex; gap: 5px; margin-bottom: 8px; align-items: center; }
.sub-label { font-size: 12px; color: #909399; margin-bottom: 4px; }
.chart-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; justify-content: center; }
/* 容器样式：白色背景，阴影，Flex布局 */
.summary-box { 
  display: flex; 
  align-items: center; 
  justify-content: space-between; 
  padding: 20px 30px; 
  background: #ffffff; 
  border: 1px solid #ebeef5; 
  border-radius: 12px; /* 圆角更大一点 */
  margin-bottom: 15px; 
  flex-shrink: 0; 
  box-shadow: 0 4px 16px rgba(0,0,0,0.06); /* 增加悬浮感 */
}

/* 单个指标项 */
.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-width: 100px;
}

.stat-item.main-stat {
  min-width: 150px;
  align-items: flex-start; /* 净利润靠左对齐 */
}

/* 标签样式 */
.stat-label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
  text-transform: uppercase; /* 英文大写显得专业 */
  letter-spacing: 0.5px;
}

.icon-help {
  cursor: help;
  font-size: 14px;
  color: #c0c4cc;
}

.icon-help:hover {
  color: #409eff;
}

/* 数值通用样式 */
.stat-value {
  font-size: 24px;
  font-weight: 700;
  font-family: 'DIN Alternate', 'Roboto', sans-serif; /* 选用数字显示好看的字体 */
  color: #303133;
  line-height: 1.2;
}

.stat-value.huge {
  font-size: 32px; /* 核心净利润更大 */
}

.unit {
  font-size: 14px;
  font-weight: normal;
  color: #909399;
  margin-left: 2px;
}

/* 分割线 */
.stat-divider {
  width: 1px;
  height: 40px;
  background-color: #e4e7ed;
  margin: 0 20px;
}

/* 右侧小数据组 */
.stat-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
  min-width: 140px;
  background: #f8f9fa;
  padding: 10px 15px;
  border-radius: 6px;
}

.sub-stat {
  display: flex;
  justify-content: space-between;
  width: 100%;
  font-size: 13px;
}

.sub-label {
  color: #909399;
}

.sub-value {
  font-weight: 600;
  font-family: 'DIN Alternate', sans-serif;
}

/* 语义化颜色 */
.text-up { color: #67c23a !important; } /* 涨/盈利/好 */
.text-down { color: #f56c6c !important; } /* 跌/亏损/差 */
.text-gold { color: #e6a23c !important; } /* 极好 */
.text-gray { color: #606266 !important; }
</style>