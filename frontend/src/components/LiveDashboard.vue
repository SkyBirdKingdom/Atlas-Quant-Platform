<template>
  <div class="live-dashboard">
    <el-card class="status-header">
      <div class="header-content">
        <div class="left">
          <el-tag :type="status === 'running' ? 'success' : 'info'" effect="dark">
            {{ status === 'running' ? '● 运行中' : '○ 已暂停' }}
          </el-tag>
          <span class="mode-tag">{{ mode }} MODE</span>
          <span class="update-time">最后更新: {{ lastUpdated || '--' }}</span>
        </div>
        <div class="right">
          <el-button-group>
            <el-button type="primary" size="small" @click="refreshStatus" :loading="loading" icon="Refresh">刷新</el-button>
            <el-button type="danger" size="small" icon="SwitchButton" disabled>紧急停止</el-button>
          </el-button-group>
        </div>
      </div>
    </el-card>

    <div class="main-metrics">
      <el-row :gutter="20">
        <el-col :span="8">
          <el-card shadow="hover" class="metric-card">
            <template #header><div class="card-title">总资产 (Equity)</div></template>
            <div class="metric-value">{{ formatMoney(equity) }} <span class="unit">€</span></div>
            <div class="metric-sub">
              现金: {{ formatMoney(cash) }} €
            </div>
          </el-card>
        </el-col>

        <el-col :span="8">
          <el-card shadow="hover" class="metric-card">
            <template #header><div class="card-title">当前净持仓 (Net Pos)</div></template>
            <div class="metric-value" :class="posClass">
              {{ position }} <span class="unit">MW</span>
            </div>
            <div class="metric-sub">
              方向: {{ position > 0 ? '多头 (Long)' : (position < 0 ? '空头 (Short)' : '空仓') }}
            </div>
          </el-card>
        </el-col>

        <el-col :span="8">
          <el-card shadow="hover" class="metric-card">
            <template #header><div class="card-title">累计成本 (Fees & Slip)</div></template>
            <div class="metric-value text-warn">{{ formatMoney(totalCost) }} <span class="unit">€</span></div>
            <div class="metric-sub">
              交易费: {{ formatMoney(fees) }} | 滑点: {{ formatMoney(slippage) }}
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <el-row :gutter="20" style="margin-top: 20px;">
      <el-col :span="14">
        <el-card class="panel-card" header="📋 活跃挂单 (Active Orders)">
          <el-table :data="activeOrders" style="width: 100%" empty-text="当前无挂单">
            <el-table-column prop="type" label="类型" width="100">
              <template #default="scope">
                <el-tag size="small">{{ scope.row.type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="target_pos" label="目标持仓" width="120" />
            <el-table-column prop="limit_price" label="限价" width="120">
              <template #default="scope">{{ scope.row.limit_price || 'MKT' }}</template>
            </el-table-column>
            <el-table-column prop="reason" label="策略信号" />
            <el-table-column prop="status" label="状态" width="100" />
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="10">
        <el-card class="panel-card" header="📟 系统终端 (Logs)">
          <div class="log-window" ref="logWindow">
            <div v-for="(log, idx) in logs" :key="idx" class="log-line">
              {{ log }}
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, computed, onUnmounted } from 'vue';
import { Refresh, SwitchButton } from '@element-plus/icons-vue';
import { getLiveStatus, getLiveLogs } from '@/api/service';

const status = ref('stopped');
const mode = ref('PAPER');
const lastUpdated = ref('');
const loading = ref(false);

const cash = ref(0);
const position = ref(0);
const equity = ref(0); // 需要后端计算或前端计算
const slippage = ref(0);
const fees = ref(0);
const activeOrders = ref([]);
const logs = ref([]);

// 计算属性
const totalCost = computed(() => slippage.value + fees.value);
const posClass = computed(() => position.value > 0 ? 'text-up' : (position.value < 0 ? 'text-down' : ''));

// API 基础路径
const API_BASE = 'http://localhost:8000/api'; // 请根据实际配置

const formatMoney = (val) => {
  return Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const refreshStatus = async () => {
  loading.value = true;
  try {
    // 1. 获取状态
    const res = await getLiveStatus();
    if (res.data.status === 'running' || res.data.data) {
      const data = res.data.data;
      status.value = 'running';
      lastUpdated.value = res.data.updated_at ? new Date(res.data.updated_at).toLocaleTimeString() : '';
      mode.value = res.data.mode || 'PAPER';
      
      cash.value = parseFloat(data.cash || 0);
      position.value = parseFloat(data.position || 0);
      
      const stats = data.stats || {};
      slippage.value = parseFloat(stats.slippage || 0);
      fees.value = parseFloat(stats.fees || 0);
      
      activeOrders.value = data.orders || [];
      
      // 估算净值 (Equity = Cash + Pos * LastPrice)
      // 由于 state.json 可能没存 last_price，这里暂时近似展示 Cash 
      // 或者您可以修改 backend TradeEngine.get_state 加上 equity 字段
      equity.value = cash.value; 
    } else {
      status.value = 'stopped';
    }

    // 2. 获取日志
    // const logRes = await getLiveLogs();
    // logs.value = logRes.data.logs || [];
    
  } catch (e) {
    console.error("Fetch status failed", e);
  } finally {
    loading.value = false;
  }
};

let timer = null;
onMounted(() => {
  // refreshStatus();
  // timer = setInterval(refreshStatus, 5000); // 每5秒轮询一次
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<style scoped>
.live-dashboard { padding: 20px; }
.status-header { margin-bottom: 20px; background: #2b303b; color: #fff; border: none; }
.header-content { display: flex; justify-content: space-between; align-items: center; }
.left { display: flex; align-items: center; gap: 15px; }
.mode-tag { font-weight: bold; background: #e6a23c; color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.update-time { font-size: 12px; color: #9aaabf; }

.metric-card { text-align: center; height: 160px; display: flex; flex-direction: column; justify-content: center; }
.card-title { font-size: 14px; color: #909399; font-weight: 500; }
.metric-value { font-size: 32px; font-weight: 700; font-family: 'DIN Alternate', sans-serif; margin: 10px 0; color: #303133; }
.unit { font-size: 14px; font-weight: normal; color: #909399; }
.metric-sub { font-size: 12px; color: #606266; background: #f4f4f5; display: inline-block; padding: 4px 10px; border-radius: 12px; }

.text-up { color: #67c23a; }
.text-down { color: #f56c6c; }
.text-warn { color: #e6a23c; }

.log-window {
  background: #1e1e1e;
  color: #00ff00;
  font-family: 'Consolas', monospace;
  font-size: 12px;
  height: 300px;
  overflow-y: auto;
  padding: 10px;
  border-radius: 4px;
}
.log-line { margin-bottom: 4px; white-space: pre-wrap; word-break: break-all; border-bottom: 1px solid #333; padding-bottom: 2px; }
</style>