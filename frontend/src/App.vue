<template>
  <div class="app-container">
    <div class="main-header">
      <h1>⚡ Nord Pool 智能风控终端</h1>
      <div class="status-badge">
        <span class="status-dot"></span> System Online
      </div>
    </div>

    <el-tabs v-model="activeTab" type="border-card" class="main-tabs">
      
      <el-tab-pane name="daily" label="📈 单日深度透视">
        <template #label>
          <span class="custom-tabs-label">
            <el-icon><DataLine /></el-icon>
            <span>单日分析 (PH/QH)</span>
          </span>
        </template>
        <RiskDashboard />
      </el-tab-pane>

      <el-tab-pane name="market" label="📊 市场微观分析">
        <template #label>
          <span class="custom-tabs-label">
            <el-icon><Histogram /></el-icon>
            <span>市场分析 (K线)</span>
          </span>
        </template>
        <MarketAnalysis />
      </el-tab-pane>

      <el-tab-pane name="range" label="📅 区间热力全景">
        <template #label>
          <span class="custom-tabs-label">
            <el-icon><Calendar /></el-icon>
            <span>区间热力图</span>
          </span>
        </template>
        <LiquidityHeatmap />
      </el-tab-pane>

      <el-tab-pane name="optimize" label="🚀 圣杯寻找">
        <template #label>
          <span class="custom-tabs-label">
            <el-icon><Compass /></el-icon>
            <span>参数优化</span>
          </span>
        </template>
        <OptimizationLab />
      </el-tab-pane>

      <el-tab-pane name="backtest" label="🧪 策略实验室">
        <template #label>
          <span class="custom-tabs-label">
            <el-icon><TrendCharts /></el-icon>
            <span>策略回测 (ROI)</span>
          </span>
        </template>
        <BacktestLab />
      </el-tab-pane>

      <el-tab-pane name="Live Trader" label="🧪 实盘交易">
        <template #label>
          <span class="custom-tabs-label">
            <el-icon><TrendCharts /></el-icon>
            <span>实盘交易</span>
          </span>
        </template>
        <LiveDashboard />
      </el-tab-pane>

      <el-tab-pane name="data" label="💾 数据同步中心">
        <template #label>
          <span class="custom-tabs-label">
            <el-icon><Setting /></el-icon>
            <span>数据管理</span>
          </span>
        </template>
        <DataManager />
      </el-tab-pane>

    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue';
// 引入 Element Plus 的图标
import { DataLine, Calendar, Setting, Histogram, TrendCharts } from '@element-plus/icons-vue';

// 引入三个核心业务组件
import RiskDashboard from './components/RiskDashboard.vue';
import LiquidityHeatmap from './components/LiquidityHeatmap.vue';
import DataManager from './components/DataManager.vue';
import BacktestLab from './components/BacktestLab.vue';
import MarketAnalysis from './components/MarketAnalysis.vue';
import OptimizationLab from './components/OptimizationLab.vue';
import LiveDashboard from './components/LiveDashboard.vue';

// 默认显示第一个 Tab
const activeTab = ref('backtest');
</script>

<style>
/* 全局重置 */
body {
  margin: 0;
  padding: 0;
  background-color: #f0f2f5;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}

.app-container {
  max-width: 1600px; /* 大屏适配 */
  margin: 0 auto;
  padding: 20px;
}

.main-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  background: white;
  padding: 15px 30px;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}

.main-header h1 {
  margin: 0;
  font-size: 24px;
  color: #1f2f3d;
  font-weight: 600;
}

.status-badge {
  font-size: 14px;
  color: #67c23a;
  display: flex;
  align-items: center;
}

.status-dot {
  width: 8px;
  height: 8px;
  background-color: #67c23a;
  border-radius: 50%;
  margin-right: 8px;
  display: inline-block;
  box-shadow: 0 0 4px #67c23a;
}

.main-tabs {
  min-height: 800px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.05);
}

.custom-tabs-label {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* 覆盖 Element Plus Tabs 的一些默认样式，使其更美观 */
.el-tabs__item {
  font-size: 16px !important;
  height: 50px !important;
  line-height: 50px !important;
}

.el-tabs__content {
  padding: 30px !important;
  background-color: #fff;
}
</style>