<template>
  <el-card class="lab-card">
    <template #header>
      <div class="card-header">
        <div class="header-title">
          <el-icon><TrendCharts /></el-icon>
          <span>智能策略实验室 (Smart Strategy Lab)</span>
        </div>
        <el-button type="info" plain icon="Clock" @click="openHistory">历史快照</el-button>
      </div>
    </template>

    <el-drawer v-model="historyVisible" title="🔬 实验记录本" size="400px">
      <div class="history-list">
        <div 
          v-for="rec in historyList" 
          :key="rec.id" 
          class="history-card"
          @click="restoreSnapshot(rec)"
        >
          <div class="h-header">
            <span class="h-date">{{ formatTime(rec.created_at) }}</span>
            <el-tag size="small" effect="plain">{{ rec.area }}</el-tag>
          </div>
          
          <div class="h-metrics">
            <div class="h-metric" :class="rec.total_pnl >= 0 ? 'text-up' : 'text-down'">
              <span class="label">PnL</span>
              <span class="value">{{ rec.total_pnl }}</span>
            </div>
            <div class="h-metric">
              <span class="label">Sharpe</span>
              <span class="value">{{ rec.sharpe_ratio }}</span>
            </div>
            <div class="h-metric">
              <span class="label">DD</span>
              <span class="value text-down">{{ rec.max_drawdown }}</span>
            </div>
          </div>
          
          <div class="h-actions">
            <el-popconfirm title="确定删除这条记录吗？" @confirm="deleteRecord(rec.id)" @click.stop>
              <template #reference>
                <el-button type="danger" link size="small" icon="Delete" @click.stop>删除</el-button>
              </template>
            </el-popconfirm>
            <el-button type="primary" link size="small" icon="RefreshLeft">加载参数</el-button>
          </div>
        </div>
      </div>
    </el-drawer>

    <div class="lab-container">
      <div class="config-panel">
        <el-scrollbar>
          <el-form label-position="top" size="small">
            <el-divider content-position="left">基础设置</el-divider>
            <el-form-item label="交易区域">
              <el-radio-group v-model="form.area" size="small" style="width: 100%">
                <el-radio-button label="SE1" /><el-radio-button label="SE2" /><el-radio-button label="SE3" /><el-radio-button label="SE4" />
              </el-radio-group>
            </el-form-item>
            
            <el-form-item label="回测区间">
              <el-date-picker v-model="form.range" type="daterange" style="width: 100%" range-separator="-" start-placeholder="Start" end-placeholder="End" value-format="YYYY-MM-DD" />
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

            <el-form-item label="高级风控">
              <el-row :gutter="10">
                <el-col :span="12">
                  <div class="sub-label">止盈 (%) (0为不限)</div>
                  <el-input-number v-model="form.params.take_profit_pct" :step="0.01" :min="0" :max="1" style="width: 100%" />
                </el-col>
                <el-col :span="12">
                  <div class="sub-label">止损 (%) (0为不限)</div>
                  <el-input-number v-model="form.params.stop_loss_pct" :step="0.01" :min="0" :max="1" style="width: 100%" />
                </el-col>
              </el-row>
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
                <div v-if="scope.row.delivery_start">
                    {{ scope.row.delivery_start.split(' ')[0] }} 
                    <b>{{ scope.row.delivery_start.split(' ')[1] }}-{{ scope.row.delivery_end }}</b>
                </div>
                <div v-else style="color: #ccc;">--</div>
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
      
      <div class="chart-wrapper" style="position: relative;">
        <div ref="chartContainer" class="lw-chart"></div>
        <div ref="toolTipRef" class="floating-tooltip"></div>
      </div>
      
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
import { ref, reactive, nextTick, onMounted, onUnmounted, markRaw } from 'vue';
import { createChart, CandlestickSeries, HistogramSeries, CrosshairMode, createSeriesMarkers } from 'lightweight-charts';
import { runBacktest, getBacktestStatus, getBacktestHistory, reproduceContract, deleteBacktestHistory } from '../api/service';
import { ElMessage } from 'element-plus';
import { TrendCharts, DataAnalysis, Plus, Delete, Top, Bottom, Right, InfoFilled } from '@element-plus/icons-vue';
import { Clock, RefreshLeft } from '@element-plus/icons-vue';

const currentRecordId = ref(null);
const historyVisible = ref(false);
const historyList = ref([]);

const loading = ref(false);
const summary = ref(null);
const contractList = ref([]);
const detailVisible = ref(false);
const currentContract = ref({});

const chartContainer = ref(null);
const toolTipRef = ref(null);
let chart = null;
let candleSeries = null;
let volumeSeries = null;

const form = reactive({
  area: 'SE3', range: ['2025-12-01', '2025-12-01'],
  params: { 
    max_pos: 2.0, 
    force_close_minutes: 10, 
    enable_slippage: true, 
    take_profit_pct: 0.05, // 默认 5% 止盈
    stop_loss_pct: 0.02    // 默认 2% 止损
 },
  rules: {
    buy: [
      { indicator: 'RSI_14', op: '<', val: 30 },
      { indicator: 'MACDh_12_26_9', op: '>', val: 0 }
    ],
    sell: [
      { indicator: 'RSI_14', op: '>', val: 70 }
    ]
  }
});

const addRule = (type) => form.rules[type].push({ indicator: 'RSI_14', op: '<', val: 0 });
const removeRule = (type, idx) => form.rules[type].splice(idx, 1);

const formatNum = (v) => Number(v).toFixed(2);
const getActionColor = (a) => a === 'BUY' ? '#67c23a' : (a === 'SELL' ? '#f56c6c' : '#909399');

const getPFColor = (pf) => {
    const val = parseFloat(pf);
    if (val >= 2.0) return 'text-gold';
    if (val >= 1.5) return 'text-up';
    if (val >= 1.0) return 'text-gray';
    return 'text-down';
};

// --- 历史记录逻辑 ---
const openHistory = async () => {
  historyVisible.value = true;
  try {
    // 假设你在 api/service.js 里加了 getBacktestHistory
    // 或者直接用 axios
    const res = await getBacktestHistory();
    historyList.value = res.data.data;
  } catch (e) {
    ElMessage.error('加载历史失败');
  }
};

const deleteRecord = async (id) => {
  try {
    await deleteBacktestHistory(id);
    ElMessage.success('删除成功');
    // 删除后刷新列表
    await openHistory();
  } catch (e) {
    ElMessage.error('删除失败');
  }
};

const formatTime = (isoStr) => {
  return new Date(isoStr).toLocaleString();
};

const showDetail = async (row) => {
  currentContract.value = row; // row 里现在只有摘要数据
  detailVisible.value = true;
  
  // 清空旧图表
  if (chart) { chart.remove(); chart = null; }
  
  // 场景 A: 刚跑完的新鲜数据 (row.chart 存在) -> 直接渲染
  if (row.chart && row.chart.length > 0) {
      await nextTick();
      renderDetailChart(row);
  } 
  // 场景 B: 历史记录恢复的数据 (row.chart 不存在) -> 调用复现接口
  else if (currentRecordId.value) {
      // 显示加载中状态...
      ElMessage.info('正在复现 K 线...');
      try {
          // 调用后端“时光机”
          const res = await reproduceContract(currentRecordId.value, row.contract_id); // 这里的 row.contract_id 对应 slim_contracts 里的 cid
          
          if (res.data.status === 'success') {
              // 补全数据
              const fullData = res.data.data;
              // 构造一个完整的 contract 对象传给 renderDetailChart
              const fullContract = {
                  ...row,
                  chart: fullData.chart,
                  details: fullData.details,
                  // 确保时间字段对齐 (后端 slim_contracts 返回的是简写 key)
                  open_time: row.open_time || row.open_t, 
                  close_time: row.close_time || row.close_t
              };
              
              currentContract.value = fullContract; // 更新弹窗绑定的数据
              await nextTick();
              renderDetailChart(fullContract);
          }
      } catch (e) {
          ElMessage.error('复现失败: ' + e.message);
      }
  }
};

const restoreSnapshot = (rec) => {
  // 1. 回填参数 (UI)
  form.area = rec.area;
  if (rec.start_date && rec.end_date) form.range = [rec.start_date, rec.end_date];
  if (rec.params && rec.params.rules) form.rules = JSON.parse(JSON.stringify(rec.params.rules));
  if (rec.params.max_pos) form.params.max_pos = rec.params.max_pos;
  
  // 2. 【核心】直接恢复结果面板 (无需重跑)
  summary.value = {
      total_pnl: rec.total_pnl,
      sharpe_ratio: rec.sharpe_ratio,
      max_drawdown: rec.max_drawdown,
      profit_factor: rec.profit_factor,
      win_rate: rec.win_rate,
      trade_count: rec.trade_count
  };
  
  // 3. 恢复合约列表 (适配字段名)
  // 数据库存的是 slim 格式 (cid, start, pnl...)，前端表格对应的是 contract_id, delivery_start...
  // 我们做一个映射
  if (rec.contract_stats) {
      contractList.value = rec.contract_stats.map(c => ({
          contract_id: c.cid,
          type: c.type,
          delivery_start: c.start,
          delivery_end: c.end,
          open_time: c.open_t,
          close_time: c.close_t,
          pnl: c.pnl,
          trade_count: c.cnt,
          slippage: c.slip,
          // chart: undefined <--- 关键：这里没有图表数据
      }));
  }
  
  // 4. 记录当前 RecordID，供后续点击详情时使用
  currentRecordId.value = rec.id;
  
  ElMessage.success('历史结果已加载 (点击合约可复现详情)');
  historyVisible.value = false;
};

const runTest = async () => {
  if (!form.range) return;
  loading.value = true;
  summary.value = null; contractList.value = [];
  
  try {
    const requestParams = { ...form.params, rules: form.rules };
    const res = await runBacktest({
      start_date: form.range[0], end_date: form.range[1], area: form.area,
      strategy_name: "DynamicConfig", 
      params: requestParams
    });
    
    if (res.data.status === 'success') {
      currentRecordId.value = null;
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

const renderDetailChart = (contract) => {
  if (!chartContainer.value) return;

  const chartInstance = createChart(chartContainer.value, {
    width: chartContainer.value.clientWidth,
    height: 350,
    layout: { background: { color: '#ffffff' }, textColor: '#333' },
    grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
    rightPriceScale: { borderColor: '#d1d4dc', visible: true },
    timeScale: {
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (time, tickMarkType, locale) => {
        const date = new Date(time * 1000);
        const hours = date.getUTCHours().toString().padStart(2, '0');
        const minutes = date.getUTCMinutes().toString().padStart(2, '0');
        return `${hours}:${minutes}`;
      }
    },
    crosshair: { mode: CrosshairMode.Normal },
  });
  
  chart = markRaw(chartInstance);

  candleSeries = markRaw(chart.addSeries(CandlestickSeries, {
    upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350',
  }));

  volumeSeries = markRaw(chart.addSeries(HistogramSeries, {
    color: '#26a69a', priceFormat: { type: 'volume' }, priceScaleId: '', 
  }));
  
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

  const candles = [];
  const volumes = [];
  const markers = [];

  contract.chart.forEach((d, idx) => {
    const ts = d.t;

    // 如果后端传了 o (Open)，说明有有效 K 线数据
    if (d.o !== undefined && d.o !== null) {
        candles.push({
          time: ts,
          open: d.o, high: d.h, low: d.l, close: d.c
        });

        volumes.push({
          time: ts,
          value: d.v,
          color: d.c >= d.o ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)'
        });

        // 标记点 (Markers)
        if (d.a === 'BUY') {
          markers.push({ time: ts, position: 'belowBar', color: '#67c23a', shape: 'arrowUp', text: 'B' });
        } else if (d.a === 'SELL') {
          markers.push({ time: ts, position: 'aboveBar', color: '#f56c6c', shape: 'arrowDown', text: 'S' });
        } else if (d.a === 'FORCE_CLOSE') {
          markers.push({ time: ts, position: 'aboveBar', color: '#7b1fa2', shape: 'arrowDown', text: 'F' });
        }
    } else {
        // 关键：为了保持时间轴连续，对于无数据的分钟，我们只推时间，不推数据
        // Lightweight Charts 会自动处理这种 "Whitespace" (留白/断层)
        // 注意：不应该推 { time: ts, value: 0 }，这会画出一条 0 的线
        // 正确做法是在 candles 数组里跳过这个时间点？
        // 不，Lightweight Charts 要求时间连续。
        // 如果想留白，其实只要不 add 数据就行。
        // 但是为了保持横轴刻度均匀，我们通常需要填充数据。
        // 既然后端已经填充了 ffill 价格，这里 d.o 应该是有值的（除非我们刚才改了 backtest.py）
        
        // 刚才的 backtest.py 修改为：即使 volume=0 也返回 OHLC。
        // 所以这里的 else 其实不会走到。所有的分钟都会有蜡烛图（平盘）。
        // 这样图表就是连续的，非常清晰。
    }
  });

  candleSeries.setData(candles);
  volumeSeries.setData(volumes);
  createSeriesMarkers(candleSeries, markers);

  chart.timeScale().fitContent();

  // Tooltip 逻辑
  chart.subscribeCrosshairMove(param => {
    const toolTip = toolTipRef.value;
    if (!toolTip) return;

    if (
      param.point === undefined || !param.time ||
      param.point.x < 0 || param.point.x > chartContainer.value.clientWidth ||
      param.point.y < 0 || param.point.y > chartContainer.value.clientHeight
    ) {
      toolTip.style.display = 'none';
      return;
    }

    toolTip.style.display = 'block';
    
    const candleData = param.seriesData.get(candleSeries);
    const volumeData = param.seriesData.get(volumeSeries);

    if (!candleData || candleData.open === undefined) {
        toolTip.style.display = 'none'; 
        return;
    }

    const date = new Date(param.time * 1000);
    const timeStr = `${date.getUTCHours().toString().padStart(2,'0')}:${date.getUTCMinutes().toString().padStart(2,'0')}`;

    let html = `<div style="color: #333; font-weight: bold; margin-bottom: 4px">${timeStr} (UTC)</div>`;
    html += `<div style="display: flex; justify-content: space-between;"><span>Open:</span> <span>${candleData.open.toFixed(2)}</span></div>`;
    html += `<div style="display: flex; justify-content: space-between;"><span>High:</span> <span>${candleData.high.toFixed(2)}</span></div>`;
    html += `<div style="display: flex; justify-content: space-between;"><span>Low:</span> <span>${candleData.low.toFixed(2)}</span></div>`;
    html += `<div style="display: flex; justify-content: space-between;"><span>Close:</span> <span>${candleData.close.toFixed(2)}</span></div>`;
    
    if(volumeData && volumeData.value !== undefined) {
        html += `<div style="display: flex; justify-content: space-between; color: #26a69a"><span>Vol:</span> <span>${volumeData.value.toFixed(1)}</span></div>`;
    }

    toolTip.innerHTML = html;

    const x = param.point.x;
    const y = param.point.y;
    const toolTipWidth = 120;
    const toolTipHeight = 130;
    const containerWidth = chartContainer.value.clientWidth;

    let left = x + 10;
    if (left + toolTipWidth > containerWidth) {
        left = x - toolTipWidth - 10;
    }
    
    let top = y + 10;
    if (top + toolTipHeight > 350) {
        top = y - toolTipHeight - 10;
    }

    toolTip.style.left = left + 'px';
    toolTip.style.top = top + 'px';
  });
};

const handleResize = () => {
  if (chart && chartContainer.value) {
    chart.applyOptions({ width: chartContainer.value.clientWidth });
  }
};

onMounted(() => window.addEventListener('resize', handleResize));
onUnmounted(() => {
  if (chart) { chart.remove(); chart = null; }
  window.removeEventListener('resize', handleResize);
});
</script>

<style scoped>
/* 保持原有布局样式 */
.lab-container { display: flex; gap: 20px; height: 800px; }
.config-panel { width: 340px; background: #f8f9fa; padding: 15px; border-radius: 8px; height: 100%; overflow-y: auto; flex-shrink: 0; }
.result-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.rule-row { display: flex; gap: 5px; margin-bottom: 8px; align-items: center; }
.sub-label { font-size: 12px; color: #909399; margin-bottom: 4px; }
.chart-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; justify-content: center; }

/* 容器样式复用 */
.summary-box { display: flex; align-items: center; justify-content: space-between; padding: 20px 30px; background: #ffffff; border: 1px solid #ebeef5; border-radius: 12px; margin-bottom: 15px; flex-shrink: 0; box-shadow: 0 4px 16px rgba(0,0,0,0.06); }
.stat-item { display: flex; flex-direction: column; align-items: center; justify-content: center; min-width: 100px; }
.stat-item.main-stat { min-width: 150px; align-items: flex-start; }
.stat-label { font-size: 13px; color: #909399; margin-bottom: 8px; display: flex; align-items: center; gap: 4px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
.icon-help { cursor: help; font-size: 14px; color: #c0c4cc; }
.icon-help:hover { color: #409eff; }
.stat-value { font-size: 24px; font-weight: 700; font-family: 'DIN Alternate', 'Roboto', sans-serif; color: #303133; line-height: 1.2; }
.stat-value.huge { font-size: 32px; }
.unit { font-size: 14px; font-weight: normal; color: #909399; margin-left: 2px; }
.stat-divider { width: 1px; height: 40px; background-color: #e4e7ed; margin: 0 20px; }
.stat-group { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; min-width: 140px; background: #f8f9fa; padding: 10px 15px; border-radius: 6px; }
.sub-stat { display: flex; justify-content: space-between; width: 100%; font-size: 13px; }
.sub-label { color: #909399; }
.sub-value { font-weight: 600; font-family: 'DIN Alternate', sans-serif; }
.text-up { color: #67c23a !important; }
.text-down { color: #f56c6c !important; }
.text-gold { color: #e6a23c !important; }
.text-gray { color: #606266 !important; }

/* 图表容器样式 */
.lw-chart { width: 100%; height: 350px; }

/* Tooltip */
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

.tt-time { font-weight: bold; margin-bottom: 5px; color: #333; text-align: center; border-bottom: 1px solid #eee; padding-bottom: 2px; }
.tt-row { display: flex; justify-content: space-between; margin-bottom: 2px; }
.tt-val { font-weight: 600; color: #333; }

/* 历史卡片样式 */
.history-card {
  border: 1px solid #eee;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  cursor: pointer;
  transition: all 0.2s;
}
.history-card:hover {
  border-color: #409eff;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}
.h-header { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 12px; color: #999; }
.h-metrics { display: flex; justify-content: space-between; margin-bottom: 8px; }
.h-metric { display: flex; flex-direction: column; align-items: center; }
.h-metric .label { font-size: 10px; color: #ccc; }
.h-metric .value { font-weight: bold; font-size: 14px; font-family: 'DIN Alternate'; }
.h-actions { text-align: right; border-top: 1px dashed #eee; padding-top: 5px; }
</style>