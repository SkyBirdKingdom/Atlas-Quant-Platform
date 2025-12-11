<template>
  <el-card class="heatmap-card">
    <template #header>
      <div class="card-header">
        <div class="header-title">
          <el-icon><Calendar /></el-icon>
          <span>{{ currentArea }} 流动性全景</span>
        </div>
        
        <div class="header-controls">
          <el-radio-group v-model="currentArea" size="small" @change="loadData" style="margin-right: 15px">
            <el-radio-button label="SE1" />
            <el-radio-button label="SE2" />
            <el-radio-button label="SE3" />
            <el-radio-button label="SE4" />
          </el-radio-group>

          <el-divider direction="vertical" />

          <el-radio-group v-model="contractType" size="small" @change="handleTypeChange">
            <el-radio-button label="PH">PH (1h)</el-radio-button>
            <el-radio-button label="QH">QH (15m)</el-radio-button>
          </el-radio-group>

          <el-divider direction="vertical" />

          <el-popover placement="bottom" title="🎨 热力图色带设置" :width="320" trigger="click">
            <template #reference>
              <el-button size="small" :icon="Setting">阈值调整</el-button>
            </template>
            
            <el-form label-position="top" size="small">
              <el-alert 
                :title="`正在调整 ${contractType} 合约的阈值`" 
                type="info" 
                :closable="false" 
                style="margin-bottom: 10px" 
              />
              
              <el-form-item label="🔴 危险阈值 (低于此值显示红色)">
                <el-input-number 
                  v-model="visualSettings.risk" 
                  :min="0" 
                  :step="5" 
                  style="width: 100%" 
                />
              </el-form-item>
              
              <el-form-item label="🟢 充裕阈值 (高于此值显示绿色)">
                <el-input-number 
                  v-model="visualSettings.safe" 
                  :min="visualSettings.risk" 
                  :step="10" 
                  style="width: 100%" 
                />
              </el-form-item>

              <div style="font-size: 12px; color: #666; margin-top: 10px;">
                * 设置会自动保存，下次访问依然生效。
              </div>
            </el-form>
          </el-popover>

          <el-divider direction="vertical" />

          <el-radio-group v-model="viewMode" size="small" @change="renderHeatmap">
            <el-radio-button label="vertical">横排</el-radio-button>
            <el-radio-button label="horizontal">竖排</el-radio-button>
          </el-radio-group>

          <el-divider direction="vertical" />

          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="-"
            start-placeholder="开始"
            end-placeholder="结束"
            value-format="YYYY-MM-DD"
            :clearable="false"
            @change="loadData"
            style="width: 220px;"
            size="small"
          />
        </div>
      </div>
    </template>
    
    <div 
      ref="chartContainer" 
      class="chart-container" 
      :style="{ height: dynamicHeight + 'px' }"
    ></div>
    
    <el-collapse style="margin-top: 20px;">
      <el-collapse-item title="查看详细数据表" name="1">
        <el-table :data="filteredTableData" style="width: 100%" height="300" stripe border size="small">
          <el-table-column prop="date" label="日期" sortable width="120" fixed />
          <el-table-column prop="hour" label="小时" sortable width="80" />
          <el-table-column prop="type" label="类型" width="80">
            <template #default="scope">
              <el-tag :type="scope.row.type === 'PH' ? '' : 'warning'" size="small">{{ scope.row.type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="volume" label="总成交量 (MW)" sortable>
            <template #default="scope">
              <span :style="{ fontWeight: 'bold', color: getVolumeColor(scope.row.volume) }">
                {{ scope.row.volume }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="volatility" label="价格波动 (Std)" sortable />
          <el-table-column label="状态" align="center">
            <template #default="scope">
               <el-tag v-if="scope.row.volume < visualSettings.risk" type="danger" effect="dark" size="small">Risk</el-tag>
               <el-tag v-else-if="scope.row.volume < visualSettings.safe" type="warning" size="small">Watch</el-tag>
               <el-tag v-else type="success" size="small">Safe</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, shallowRef, reactive, watch } from 'vue';
import * as echarts from 'echarts';
import { getRangeAnalysis } from '../api/service';
import { Calendar, Setting } from '@element-plus/icons-vue';

// --- 常量定义：默认配置 ---
const DEFAULT_SETTINGS = {
  PH: { risk: 50, safe: 200 },
  QH: { risk: 10, safe: 50 }
};

// --- 状态 ---
const currentArea = ref('SE3');
const dateRange = ref(['2025-12-01', '2025-12-07']);
const viewMode = ref('vertical');
const contractType = ref('PH');
const dynamicHeight = ref(600);
const chartContainer = ref(null);

// 响应式对象：当前的视觉阈值
const visualSettings = reactive({
  risk: 50,
  safe: 200
});

const myChart = shallowRef(null);
let cachedRawData = [];
const filteredTableData = ref([]);

let resizeObserver = null;

// --- 持久化逻辑 (LocalStorage) ---
const STORAGE_KEY = 'nordpool_heatmap_settings_v1';

// 读取配置
const loadSettings = (type) => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (parsed[type]) {
        // 如果有保存过，应用保存的值
        visualSettings.risk = parsed[type].risk;
        visualSettings.safe = parsed[type].safe;
        return;
      }
    }
  } catch (e) {
    console.warn('读取配置失败，使用默认值');
  }
  // 如果没保存过，使用默认值
  visualSettings.risk = DEFAULT_SETTINGS[type].risk;
  visualSettings.safe = DEFAULT_SETTINGS[type].safe;
};

// 保存配置
const saveSettings = () => {
  try {
    // 先读取旧的，以免覆盖另一个类型的数据
    const saved = localStorage.getItem(STORAGE_KEY);
    let data = saved ? JSON.parse(saved) : {};
    
    // 更新当前类型的配置
    data[contractType.value] = {
      risk: visualSettings.risk,
      safe: visualSettings.safe
    };
    
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch (e) {
    console.error('保存配置失败', e);
  }
};

// --- 监听 ---
onMounted(() => {
  // 1. 初始化时，先读取 PH 的配置
  loadSettings('PH');
  
  loadData();
  
  if (chartContainer.value) {
    resizeObserver = new ResizeObserver(() => myChart.value && myChart.value.resize());
    resizeObserver.observe(chartContainer.value);
  }
});

onUnmounted(() => {
  if (myChart.value) myChart.value.dispose();
  if (resizeObserver) resizeObserver.disconnect();
});

// 监听 visualSettings 变化 -> 自动保存 + 自动重绘
watch(visualSettings, () => {
  saveSettings(); // 保存到 LocalStorage
  renderHeatmap(); // 重绘图表
}, { deep: true });

// --- 核心逻辑 ---

const loadData = async () => {
  if (myChart.value) myChart.value.showLoading();
  try {
    const res = await getRangeAnalysis({
      start_date: dateRange.value[0],
      end_date: dateRange.value[1],
      area: currentArea.value
    });
    cachedRawData = res.data.data;
    renderHeatmap();
  } catch (error) {
    console.error(error);
  } finally {
    if (myChart.value) myChart.value.hideLoading();
  }
};

const handleTypeChange = (val) => {
  // 切换类型时，从 LocalStorage 读取该类型的配置
  // 而不是粗暴地重置为默认值
  loadSettings(val);
  
  // loadSettings 会修改 visualSettings，从而触发上面的 watch，进而触发 renderHeatmap
  // 所以这里不需要手动调 renderHeatmap
};

const renderHeatmap = () => {
  if (!chartContainer.value) return;
  if (!myChart.value) myChart.value = echarts.init(chartContainer.value);

  const currentData = cachedRawData.filter(d => d.type === contractType.value);
  filteredTableData.value = currentData;

  if (currentData.length === 0) {
    myChart.value.clear();
    return;
  }

  const hours = Array.from({length: 24}, (_, i) => `${i}:00`);
  const dates = [...new Set(currentData.map(item => item.date))].sort();

  if (viewMode.value === 'horizontal') {
    dynamicHeight.value = Math.max(500, dates.length * 35 + 150);
  } else {
    dynamicHeight.value = 600;
  }

  nextTick(() => {
    myChart.value.resize();
    
    const seriesData = currentData.map(item => {
      if (viewMode.value === 'vertical') {
        return [dates.indexOf(item.date), item.hour, item.volume];
      } else {
        return [item.hour, dates.indexOf(item.date), item.volume];
      }
    });

    const { risk, safe } = visualSettings;

    const option = {
      title: { 
        text: `SE3 ${contractType.value} 市场深度热力图`, 
        subtext: `Risk < ${risk}MW | Safe > ${safe}MW`,
        left: 'center', top: 5 
      },
      tooltip: {
        position: 'top',
        formatter: (p) => {
          const val = p.value[2];
          return `<b>${p.name}</b><br/>类型: ${contractType.value}<br/>成交量: <b>${val} MW</b>`;
        }
      },
      animation: false,
      grid: { top: 60, bottom: 80, left: 80, right: 30, containLabel: true },
      xAxis: {
        type: 'category',
        data: viewMode.value === 'vertical' ? dates : hours,
        splitArea: { show: true },
        axisLabel: { rotate: viewMode.value === 'vertical' ? 45 : 0 }
      },
      yAxis: {
        type: 'category',
        data: viewMode.value === 'vertical' ? hours : dates,
        splitArea: { show: true }
      },
      visualMap: {
        min: 0,
        max: safe,
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 10,
        type: 'piecewise', 
        pieces: [
            {min: safe, label: `> ${safe} MW (充裕)`, color: '#50a3ba'},
            {min: risk, max: safe, label: '观察区间', color: '#eac736'},
            {max: risk, label: `< ${risk} MW (高危)`, color: '#d94e5d'}
        ],
        itemWidth: 20,
        itemHeight: 14
      },
      series: [{
        type: 'heatmap',
        data: seriesData,
        label: { show: true, fontSize: 10 },
        itemStyle: { borderWidth: 1, borderColor: '#fff' }
      }]
    };
    
    myChart.value.setOption(option, true);
  });
};

const getVolumeColor = (val) => {
  if (val < visualSettings.risk) return '#f56c6c';
  if (val < visualSettings.safe) return '#e6a23c';
  return '#67c23a';
};
</script>

<style scoped>
.header-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.chart-container {
  width: 100%;
  transition: height 0.3s ease;
}
:deep(.el-input-number.is-controls-right .el-input__wrapper) {
  padding-left: 0;
  padding-right: 30px;
}
</style>