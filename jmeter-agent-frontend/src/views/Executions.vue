<template>
  <div class="executions-page">
    <div class="split-layout">
      <!-- Left Panel: Project List -->
      <div class="split-left">
        <div class="panel-header">
          <span>{{ t('executions.projectList') }}</span>
        </div>
        <div class="project-list" v-loading="loadingProjects">
          <div
            v-for="project in projects"
            :key="project.id"
            :class="['project-item', { active: selectedProjectId === project.id }]"
            @click="selectProject(project.id)"
          >
            <div class="project-info">
              <div class="project-name">{{ project.name }}</div>
              <div class="project-meta">
                <span>{{ t('executions.executionCount', { count: project.execution_count || 0 }) }}</span>
              </div>
            </div>
          </div>
          <el-empty v-if="!loadingProjects && projects.length === 0" :description="t('executions.noProjects')" :image-size="60" />
        </div>
      </div>

      <!-- Right Panel: Executions -->
      <div class="split-right">
        <template v-if="!selectedProjectId">
          <el-empty :description="t('executions.selectProject')" :image-size="80" />
        </template>

        <template v-else>
          <div class="execution-header">
            <div class="header-left">
              <h3>{{ t('executions.title', { projectName: selectedProject?.name }) }}</h3>
            </div>
            <el-button text type="primary" @click="loadExecutions">
              <el-icon><Refresh /></el-icon> {{ t('executions.refresh') }}
            </el-button>
          </div>

          <el-card shadow="never" class="execution-table-card">
            <div class="table-scroll">
            <el-table :data="executions" v-loading="loadingExecutions" stripe class="executions-table">
              <el-table-column label="ID" width="110" class-name="col-id">
              <template #default="{ row }">
                <span class="id-text">{{ row.id }}</span>
              </template>
            </el-table-column>
              <el-table-column prop="test_plan_id" :label="t('executions.table.plan')" min-width="100" show-overflow-tooltip class-name="col-plan" />
              <el-table-column :label="t('executions.table.status')" width="120" class-name="col-status">
                <template #default="{ row }">
                  <el-tag :type="statusType(row.status)">
                    <el-icon v-if="row.status === 'running'" class="is-loading"><Loading /></el-icon>
                    {{ statusText(row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column :label="t('executions.table.progress')" width="150" class-name="col-progress">
                <template #default="{ row }">
                  <el-progress
                    :percentage="row.progress || 0"
                    :status="row.status === 'failed' ? 'exception' : undefined"
                  />
                </template>
              </el-table-column>
              <el-table-column prop="start_time" :label="t('executions.table.startTime')" width="160" class-name="col-time">
                <template #default="{ row }">
                  {{ formatTime(row.start_time) }}
                </template>
              </el-table-column>
              <el-table-column :label="t('executions.table.duration')" width="100" class-name="col-duration">
                <template #default="{ row }">
                  {{ row.end_time ? formatDuration(row.start_time, row.end_time) : '-' }}
                </template>
              </el-table-column>
              <el-table-column :label="t('executions.table.operation')" min-width="200" class-name="col-actions">
                <template #default="{ row }">
                  <div class="action-buttons">
                    <el-button
                      v-if="row.status === 'running'"
                      text
                      type="warning"
                      size="small"
                      @click="cancel(row.id)"
                    >
                      {{ t('executions.cancel') }}
                    </el-button>
                    <el-button
                      v-if="row.status === 'completed'"
                      text
                      type="primary"
                      size="small"
                      @click="viewReport(row.id)"
                    >
                      {{ t('executions.viewReport') }}
                    </el-button>
                    <el-button text size="small" @click="viewLog(row.id)">{{ t('executions.log') }}</el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>
          <el-empty v-if="!loadingExecutions && executions.length === 0" :description="t('executions.noRecords')" :image-size="60" />
          </el-card>
        </template>
      </div>
    </div>

    <!-- Log Dialog -->
    <el-dialog v-model="logVisible" :title="t('executions.logDialog')" width="800px" style="max-width: calc(100vw - 32px);">
      <pre class="log-content">{{ logContent }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { useI18n } from '@/composables/useI18n'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const projects = ref<any[]>([])
const selectedProjectId = ref<string | null>(null)
const selectedProject = ref<any>(null)
const executions = ref<any[]>([])
const loadingProjects = ref(false)
const loadingExecutions = ref(false)
const logVisible = ref(false)
const logContent = ref('')
let pollTimer: any = null

const loadProjects = async () => {
  loadingProjects.value = true
  try {
    const res = await api.getProjects()
    projects.value = res.data?.projects || res.data || []
  } catch {
    projects.value = []
  } finally {
    loadingProjects.value = false
  }
}

const selectProject = async (projectId: string) => {
  selectedProjectId.value = projectId
  selectedProject.value = projects.value.find(p => p.id === projectId)
  await loadExecutions()
}

const loadExecutions = async () => {
  if (!selectedProjectId.value) return
  loadingExecutions.value = true
  try {
    const res = await api.getProjectExecutions(selectedProjectId.value)
    executions.value = res.data || []
  } catch {
    executions.value = []
  } finally {
    loadingExecutions.value = false
  }
}

const cancel = async (id: string) => {
  try {
    await api.cancelExecution(id)
    ElMessage.success(t('executions.cancelled'))
    await loadExecutions()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('executions.cancelFailed'))
  }
}

const viewReport = (id: string) => {
  router.push(`/reports/${id}`)
}

const viewLog = async (id: string) => {
  try {
    const res = await api.getLog(id, 200)
    logContent.value = res.data.log || t('executions.noLog')
    logVisible.value = true
  } catch {
    logContent.value = t('executions.logReadError')
    logVisible.value = true
  }
}

const statusType = (status: string) => {
  const map: Record<string, string> = {
    pending: 'info',
    running: 'warning',
    completed: 'success',
    failed: 'danger',
    cancelled: ''
  }
  return map[status] || ''
}

const statusMap = computed<Record<string, string>>(() => ({
  pending: t('executions.status.pending'),
  running: t('executions.status.running'),
  completed: t('executions.status.completed'),
  failed: t('executions.status.failed'),
  cancelled: t('executions.status.cancelled')
}))

const statusText = (status: string) => {
  return statusMap.value[status] || status
}

const formatTime = (time: string) => {
  if (!time) return '-'
  return new Date(time).toLocaleString()
}

const formatDuration = (start: string, end: string) => {
  const diff = new Date(end).getTime() - new Date(start).getTime()
  const minutes = Math.floor(diff / 60000)
  const seconds = Math.floor((diff % 60000) / 1000)
  return `${minutes}m ${seconds}s`
}

onMounted(async () => {
  await loadProjects()

  // Check query params for pre-selected project
  const queryProjectId = route.query.projectId as string
  if (queryProjectId && projects.value.find(p => p.id === queryProjectId)) {
    await selectProject(queryProjectId)
  } else if (projects.value.length > 0) {
    await selectProject(projects.value[0].id)
  }

  // Poll for running executions
  pollTimer = setInterval(() => {
    if (selectedProjectId.value) {
      loadExecutions()
    }
  }, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped lang="scss">
.executions-page {
  height: calc(100vh - var(--layout-offset, 132px));
  min-height: 480px;
  max-width: 1400px;
  margin: 0 auto;
  box-sizing: border-box;

  @media (max-width: 768px) {
    padding: 0 16px;
  }
}

.split-layout {
  display: flex;
  gap: 18px;
  height: 100%;

  @media (max-width: 768px) {
    flex-direction: column;
    gap: 16px;
    height: auto;
  }
}

.split-left {
  width: 280px;
  min-width: 220px;
  max-width: 35%;
  flex-shrink: 0;
  background: var(--tavily-bg-card);
  border: 1px solid var(--tavily-border);
  border-radius: var(--tavily-radius-xl);
  display: flex;
  flex-direction: column;
  overflow: hidden;

  @media (max-width: 768px) {
    width: 100%;
    max-width: none;
  }

  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 16px;
    border-bottom: 1px solid var(--tavily-border);
    font-size: 14px;
    font-weight: 500;
    color: var(--tavily-text-primary);
  }

  .project-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;

    .project-item {
      display: flex;
      align-items: center;
      padding: 12px 14px;
      border-radius: var(--tavily-radius-md);
      cursor: pointer;
      transition: all 0.15s ease;
      margin-bottom: 4px;

      &:hover {
        background: var(--tavily-primary-bg);
      }

      &.active {
        background: var(--tavily-primary);
        color: #fff;

        .project-info {
          .project-name { color: #fff; }
          .project-meta { color: rgba(255,255,255,0.75); }
        }
      }

      .project-info {
        flex: 1;
        min-width: 0;

        .project-name {
          font-size: 14px;
          font-weight: 500;
          color: var(--tavily-text-primary);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .project-meta {
          font-size: 12px;
          color: var(--tavily-text-muted);
        }
      }
    }
  }
}

.split-right {
  flex: 1;
  overflow-y: auto;
  background: var(--tavily-bg-card);
  border: 1px solid var(--tavily-border);
  border-radius: var(--tavily-radius-xl);
  padding: 24px;

  @media (max-width: 768px) {
    padding: 16px;
  }

  .el-empty {
    margin-top: 60px;
  }
}

.execution-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--tavily-border);

  .header-left {
    min-width: 0;
  }

  h3 {
    margin: 0;
    font-size: 18px;
    font-weight: 500;
    color: var(--tavily-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

.execution-table-card {
  border: none;
  box-shadow: none;
}

.log-content {
  background: var(--tavily-bg-code);
  color: var(--tavily-text-secondary);
  padding: 18px;
  border-radius: var(--tavily-radius-md);
  max-height: 500px;
  overflow-y: auto;
  font-family: 'SF Mono', 'Cascadia Code', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.5;
  border: 1px solid var(--tavily-border);
}

.table-scroll {
  overflow-x: auto;

  .executions-table {
    min-width: 700px;
  }
}

.id-text {
  display: inline-block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

:deep(.el-table .col-id .cell) {
  white-space: nowrap !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  word-break: keep-all !important;
}

.action-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

@media (max-width: 768px) {
  .col-id,
  .col-progress,
  .col-duration {
    display: none;
  }

  .col-plan {
    min-width: 80px;
  }

  .col-status {
    width: 90px;
  }

  .col-time {
    width: 140px;
  }
}
</style>
