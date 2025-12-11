<template>
  <div class="data-manager-container">
    <el-card>
      <template #header>
        <div class="card-header">
          <div class="header-title">
            <el-icon class="header-icon"><Setting /></el-icon>
            <span>数据资产管理中心</span>
            <el-tag type="success" effect="plain" round size="small" style="margin-left: 10px;">
              <el-icon style="display: inline;"><Clock /></el-icon> 自动同步：运行中
            </el-tag>
          </div>
          
          <el-button type="primary" :loading="isFetching" @click="openFetchDialog">
            <el-icon style="margin-right: 5px"><Refresh /></el-icon>
            手动补录数据
          </el-button>
        </div>
      </template>

      <div class="status-panel">
        <div class="panel-title">
          <span>🤖 自动同步机器人状态</span>
          <el-button link type="primary" size="small" @click="refreshAll" icon="Refresh">刷新状态</el-button>
        </div>
        
        <el-table :data="systemStatus" style="width: 100%" size="small" border stripe>
          <el-table-column prop="area" label="区域" width="80" align="center" sortable />
          <el-table-column prop="status" label="运行状态" width="120" align="center">
            <template #default="scope">
              <el-tag v-if="scope.row.status === 'ok'" type="success" effect="dark">正常</el-tag>
              <el-tag v-else-if="scope.row.status === 'running'" type="primary" effect="dark">同步中...</el-tag>
              <el-tag v-else-if="scope.row.status === 'warning'" type="warning" effect="dark">部分异常</el-tag>
              <el-tag v-else type="danger" effect="dark">错误中断</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="last_fetched_time" label="已归档至 (安全线)" width="180">
            <template #default="scope">
              <span style="font-family: monospace;">{{ formatDate(scope.row.last_fetched_time) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="updated_at" label="最后心跳时间" width="180">
            <template #default="scope">
              <span>{{ formatDate(scope.row.updated_at) }}</span>
              <el-tag v-if="isOutdated(scope.row.updated_at)" type="danger" size="small" style="margin-left:5px">延迟</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="last_error" label="错误日志">
            <template #default="scope">
              <span v-if="scope.row.last_error" class="error-text">
                {{ scope.row.last_error }}
              </span>
              <span v-else style="color: #c0c4cc;">-</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-divider />

      <div class="logs-section" style="margin-top: 20px;">
        <div class="panel-title">
          <span>📜 系统运行日志 (Live Logs)</span>
          <el-button link type="primary" size="small" @click="refreshLogs" icon="Refresh">刷新日志</el-button>
        </div>
        
        <div class="log-terminal" ref="logTerminal">
          <div v-for="(line, index) in logs" :key="index" class="log-line">
            {{ line }}
          </div>
          <div v-if="logs.length === 0" class="log-empty">暂无日志...</div>
        </div>
      </div>

      <el-divider />

      <div class="calendar-section">
        <div class="calendar-header">
          <div class="calendar-title-group">
            <span>📅 数据分布日历</span>
            
            <el-radio-group v-model="currentViewArea" size="small" @change="loadCalendar" style="margin-left: 20px;">
              <el-radio-button label="SE1" />
              <el-radio-button label="SE2" />
              <el-radio-button label="SE3" />
              <el-radio-button label="SE4" />
            </el-radio-group>
          </div>

          <div class="legend">
            <div class="legend-item"><span class="dot archive"></span> ✅ 已归档</div>
            <div class="legend-item"><span class="dot active"></span> 🔄 更新中</div>
            <div class="legend-item"><span class="dot missing"></span> ⚠️ 缺失</div>
          </div>
        </div>

        <el-calendar v-model="calendarDate" class="custom-calendar">
          <template #date-cell="{ data }">
            <div 
              class="calendar-cell" 
              :class="getCellClass(data.day, getCount(data.day))"
            >
              <div class="cell-top">
                <span class="day-num">{{ data.day.split('-')[2] }}</span>
                <span v-if="getCount(data.day) > 0" class="count-badge">
                  {{ formatCount(getCount(data.day)) }}
                </span>
              </div>
              
              <div class="cell-bottom">
                 <span v-if="getCellStatusText(data.day, getCount(data.day)) === 'Archive'" style="color: #67c23a">完整</span>
                 <span v-else-if="getCellStatusText(data.day, getCount(data.day)) === 'Active'" style="color: #409eff">更新中</span>
                 <span v-else-if="getCellStatusText(data.day, getCount(data.day)) === 'Missing'" style="color: #f56c6c; font-weight:bold">缺失</span>
              </div>
            </div>
          </template>
        </el-calendar>
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" title="手动同步 Nord Pool 数据" width="450px">
      <el-alert 
        title="注意：手动同步通常用于补录历史遗漏数据，或强制刷新未来数据。" 
        type="info" 
        :closable="false" 
        style="margin-bottom: 20px;" 
        show-icon
      />
      
      <el-form label-position="top">
        <el-form-item label="选择区域">
          <el-checkbox-group v-model="fetchForm.areas">
            <el-checkbox label="SE1" />
            <el-checkbox label="SE2" />
            <el-checkbox label="SE3" />
            <el-checkbox label="SE4" />
          </el-checkbox-group>
        </el-form-item>
        
        <el-form-item label="选择时间段">
          <el-date-picker
            v-model="fetchForm.range"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="startFetch" :loading="isFetching">
          立即执行
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue';
import { getDataCalendar, triggerFetch, getSystemStatus, getTaskStatus } from '../api/service';
import { getAppLogs } from '../api/service';
import { ElMessage } from 'element-plus';
import { Setting, Refresh, Clock } from '@element-plus/icons-vue';
import dayjs from 'dayjs';

// --- 状态变量 ---
const logs = ref([]);
const logTerminal = ref(null);

const calendarDate = ref(new Date());
const currentViewArea = ref('SE3'); // ⭐ 新增：当前查看的区域
const availableData = ref({}); 
const systemStatus = ref([]);  
const dialogVisible = ref(false);
const isFetching = ref(false);

const fetchForm = reactive({
  areas: ['SE3'], // 手动抓取默认选 SE3
  range: []
});

let statusInterval = null;

const refreshLogs = async () => {
  try {
    const res = await getAppLogs();
    logs.value = res.data.logs;
    
    // 自动滚动到底部
    setTimeout(() => {
      if (logTerminal.value) {
        logTerminal.value.scrollTop = logTerminal.value.scrollHeight;
      }
    }, 100);
  } catch (e) {
    console.error(e);
  }
};

// --- 1. 数据加载 ---
const loadCalendar = async () => {
  try {
    // ⭐ 修改：使用 currentViewArea 动态获取对应区域的数据
    const res = await getDataCalendar(currentViewArea.value);
    availableData.value = res.data;
  } catch (e) {
    console.error("日历加载失败", e);
    availableData.value = {};
  }
};

const loadStatus = async () => {
  try {
    const res = await getSystemStatus();
    systemStatus.value = res.data;
  } catch (e) {
    console.error("状态加载失败", e);
  }
};

const refreshAll = () => {
  loadCalendar(); // 这里会自动使用 currentViewArea
  loadStatus();
};

// --- 2. 日历逻辑 (保持不变) ---
const getCount = (dayStr) => availableData.value[dayStr] || 0;

const formatCount = (num) => {
  if (num > 1000) return (num / 1000).toFixed(1) + 'k';
  return num;
};

const getDateType = (dayStr) => {
  const today = dayjs().startOf('day');
  const target = dayjs(dayStr);
  if (target.isBefore(today)) return 'past';
  return 'future'; 
};

const getCellClass = (dayStr, count) => {
  const type = getDateType(dayStr);
  if (count > 0) {
    return type === 'past' ? 'status-archive' : 'status-active';
  } else {
    return type === 'past' ? 'status-missing' : 'status-empty';
  }
};

const getCellStatusText = (dayStr, count) => {
  const type = getDateType(dayStr);
  if (count > 0) return type === 'past' ? 'Archive' : 'Active';
  if (type === 'past') return 'Missing';
  return '';
};

// --- 3. 状态表格逻辑 (保持不变) ---
const formatDate = (str) => {
  if (!str) return '-';
  return dayjs(str).format('MM-DD HH:mm');
};

const isOutdated = (str) => {
  if (!str) return true;
  return dayjs().diff(dayjs(str), 'hour') > 2;
};

// --- 4. 手动补录逻辑 (保持不变) ---
const openFetchDialog = () => {
  const end = dayjs().format('YYYY-MM-DD');
  const start = dayjs().subtract(1, 'day').format('YYYY-MM-DD');
  fetchForm.range = [start, end];
  dialogVisible.value = true;
};

const startFetch = async () => {
  if (!fetchForm.range || fetchForm.range.length < 2) {
    ElMessage.warning('请选择时间段');
    return;
  }
  if (fetchForm.areas.length === 0) {
    ElMessage.warning('请至少选择一个区域');
    return;
  }

  isFetching.value = true;
  dialogVisible.value = false;

  try {
    const res = await triggerFetch({
      start_time: `${fetchForm.range[0]}T00:00:00Z`,
      end_time: `${fetchForm.range[1]}T23:59:59Z`,
      areas: fetchForm.areas
    });

    const taskId = res.data.task_id;
    ElMessage.success('任务已提交，后台正在执行...');

    const pollInterval = setInterval(async () => {
      try {
        const statusRes = await getTaskStatus(taskId);
        const status = statusRes.data.status;

        if (status === 'completed') {
          clearInterval(pollInterval);
          isFetching.value = false;
          ElMessage.success('数据同步完成！');
          refreshAll();
        } else if (status === 'failed') {
          clearInterval(pollInterval);
          isFetching.value = false;
          ElMessage.error('数据同步失败，请检查日志');
          refreshAll();
        }
      } catch (e) {
        clearInterval(pollInterval);
        isFetching.value = false;
      }
    }, 2000); 

  } catch (error) {
    isFetching.value = false;
    ElMessage.error('请求发送失败');
  }
};

// --- 生命周期 ---
onMounted(() => {
  refreshAll();
  refreshLogs();
  statusInterval = setInterval(loadStatus, 30000);
});

onUnmounted(() => {
  if (statusInterval) clearInterval(statusInterval);
});
</script>

<style scoped>
/* 样式部分保持不变，仅增加标题组样式 */
.calendar-title-group {
  display: flex;
  align-items: center;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.header-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}
.header-icon { font-size: 18px; }

.status-panel { margin-bottom: 25px; }
.panel-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  font-weight: 500;
  color: #606266;
}
.error-text {
  color: #f56c6c; font-size: 12px;
  word-break: break-all;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.calendar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.legend {
  display: flex;
  gap: 15px;
  font-size: 12px;
  color: #606266;
}
.legend-item { display: flex; align-items: center; gap: 5px; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot.archive { background: #e1f3d8; border: 1px solid #67c23a; }
.dot.active { background: #d9ecff; border: 1px solid #409eff; }
.dot.missing { background: #fde2e2; border: 1px solid #f56c6c; }

:deep(.el-calendar-table .el-calendar-day) { padding: 0; height: 85px; }
.calendar-cell {
  height: 100%; padding: 8px;
  display: flex; flex-direction: column; justify-content: space-between;
  transition: all 0.2s; border: 2px solid transparent;
}
.calendar-cell:hover { border-color: rgba(0,0,0,0.1); }
.cell-top { display: flex; justify-content: space-between; align-items: flex-start; }
.day-num { font-weight: bold; font-size: 14px; }
.count-badge { font-size: 10px; background: rgba(0,0,0,0.06); padding: 2px 5px; border-radius: 10px; color: #606266; }
.cell-bottom { text-align: right; font-size: 12px; }

.status-archive { background-color: #f0f9eb; }
.status-active { background-color: #ecf5ff; }
.status-missing { background-color: #fef0f0; border: 1px dashed #f56c6c; }
.status-empty { background-color: #fff; color: #ebeef5; }

.log-terminal {
  background: #1e1e1e; /* 黑色背景，模仿终端 */
  color: #d4d4d4;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  padding: 15px;
  border-radius: 6px;
  height: 200px;
  overflow-y: auto;
  white-space: pre-wrap; /* 保持换行 */
  border: 1px solid #333;
}

.log-line {
  line-height: 1.5;
  border-bottom: 1px solid #2a2a2a;
}
/* 给 INFO, ERROR 加点颜色 (简单匹配) */
.log-line:contains("ERROR") { color: #f56c6c; } /* 注意：CSS直接匹配文本需要JS辅助，这里仅作示意 */
</style>