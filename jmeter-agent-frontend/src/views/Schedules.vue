<template>
  <div class="schedules-page">
    <div class="page-header">
      <div class="header-left">
        <h2>{{ t('schedules.title') }}</h2>
        <p class="subtitle">{{ t('schedules.subtitle') }}</p>
      </div>
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon> {{ t('schedules.create') }}
      </el-button>
    </div>

    <el-card shadow="never" class="main-card">
      <div class="filter-bar">
        <el-select
          v-model="selectedProjectId"
          :placeholder="t('schedules.selectProject')"
          class="filter-select"
          @change="loadSchedules"
        >
          <el-option label="全部项目" value="" />
          <el-option
            v-for="project in projects"
            :key="project.id"
            :label="project.name"
            :value="project.id"
          />
        </el-select>
        <el-button text @click="loadSchedules">
          <el-icon><Refresh /></el-icon> {{ t('general.refresh') }}
        </el-button>
      </div>

      <div class="table-scroll">
      <el-table :data="schedules" v-loading="loading" stripe class="schedules-table">
        <el-table-column prop="id" :label="t('general.id')" width="70" class-name="col-id" />
        <el-table-column prop="name" :label="t('general.name')" min-width="100" show-overflow-tooltip class-name="col-name" />
        <el-table-column prop="project_id" :label="t('schedules.project')" width="100" show-overflow-tooltip class-name="col-project">
          <template #default="{ row }">
            {{ getProjectName(row.project_id) }}
          </template>
        </el-table-column>
        <el-table-column prop="test_plan_id" :label="t('schedules.testPlan')" width="100" show-overflow-tooltip class-name="col-plan">
          <template #default="{ row }">
            {{ row.test_plan_id || t('general.none') }}
          </template>
        </el-table-column>
        <el-table-column prop="cron_expression" :label="t('schedules.cron')" min-width="140" class-name="col-cron">
          <template #default="{ row }">
            <span class="cron-value">{{ row.cron_expression }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('schedules.nextRun')" width="140" class-name="col-next">
          <template #default="{ row }">
            {{ row.next_run_at ? formatTime(row.next_run_at) : '-' }}
          </template>
        </el-table-column>
        <el-table-column :label="t('schedules.lastRun')" width="140" class-name="col-last">
          <template #default="{ row }">
            {{ row.last_run_at ? formatTime(row.last_run_at) : '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="run_count" :label="t('schedules.runCount')" width="90" class-name="col-count" />
        <el-table-column :label="t('general.status')" width="90" class-name="col-status">
          <template #default="{ row }">
            <el-tag :type="row.is_enabled ? 'success' : 'info'">
              {{ row.is_enabled ? t('general.enabled') : t('general.disabled') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('general.operation')" min-width="180" class-name="col-actions">
          <template #default="{ row }">
            <div class="action-buttons">
              <el-button
                text
                type="primary"
                size="small"
                @click="toggleSchedule(row.id, row.is_enabled)"
              >
                {{ row.is_enabled ? t('general.disable') : t('general.enable') }}
              </el-button>
              <el-button text size="small" @click="editSchedule(row)">
                <el-icon><Edit /></el-icon>
              </el-button>
              <el-button text type="danger" size="small" @click="deleteSchedule(row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      </div>

      <el-empty
        v-if="!loading && schedules.length === 0"
        :description="t('schedules.empty')"
        :image-size="60"
      />
    </el-card>

    <el-dialog
      v-model="showCreateDialog"
      :title="editingSchedule ? t('schedules.editTitle') : t('schedules.createTitle')"
      width="500px"
      style="max-width: calc(100vw - 32px);"
    >
      <el-form :model="form" label-width="100px">
        <el-form-item :label="t('general.name')" required>
          <el-input v-model="form.name" :placeholder="t('schedules.namePlaceholder')" />
        </el-form-item>
        <el-form-item :label="t('schedules.project')" required>
          <el-select v-model="form.project_id" :placeholder="t('schedules.selectProject')">
            <el-option
              v-for="project in projects"
              :key="project.id"
              :label="project.name"
              :value="project.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('schedules.testPlan')">
          <el-select v-model="form.test_plan_id" :placeholder="t('schedules.selectTestPlan')">
            <el-option label="不指定" value="" />
            <el-option
              v-for="plan in projectTestPlans"
              :key="plan.id"
              :label="plan.name"
              :value="plan.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('schedules.cron')" required>
          <el-input v-model="form.cron_expression" :placeholder="t('schedules.cronPlaceholder')" />
          <div class="cron-hints">
            <div class="hint-title">{{ t('schedules.cronExamples') }}</div>
            <div class="hint-item">0 */6 * * * - {{ t('schedules.every6Hours') }}</div>
            <div class="hint-item">30 2 * * 1 - {{ t('schedules.monday230') }}</div>
            <div class="hint-item">0 9 * * 1-5 - {{ t('schedules.weekdays9am') }}</div>
          </div>
        </el-form-item>
        <el-form-item :label="t('general.enabled')">
          <el-switch v-model="form.is_enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">{{ t('general.cancel') }}</el-button>
        <el-button type="primary" @click="saveSchedule">{{ t('general.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useI18n } from '@/composables/useI18n'
import { Plus, Refresh, Edit, Delete } from '@element-plus/icons-vue'

const { t } = useI18n()

const schedules = ref<any[]>([])
const projects = ref<any[]>([])
const projectTestPlans = ref<any[]>([])
const loading = ref(false)
const showCreateDialog = ref(false)
const selectedProjectId = ref('')

const editingSchedule = ref<any>(null)
const form = ref({
  name: '',
  project_id: '',
  test_plan_id: '',
  cron_expression: '',
  is_enabled: true
})

const loadProjects = async () => {
  try {
    const res = await api.getProjects()
    projects.value = res.data?.projects || res.data || []
  } catch {
    projects.value = []
  }
}

const loadSchedules = async () => {
  loading.value = true
  try {
    const res = await api.getSchedules(selectedProjectId.value || undefined)
    schedules.value = res.data || []
  } catch {
    schedules.value = []
  } finally {
    loading.value = false
  }
}

const loadProjectTestPlans = async (projectId: string) => {
  try {
    const res = await api.getProjectTestPlans(projectId)
    projectTestPlans.value = res.data || []
  } catch {
    projectTestPlans.value = []
  }
}

const getProjectName = (projectId: string) => {
  const project = projects.value.find(p => p.id === projectId)
  return project?.name || projectId
}

const formatTime = (time: string) => {
  if (!time) return '-'
  return new Date(time).toLocaleString()
}

const toggleSchedule = async (id: number, isEnabled: boolean) => {
  try {
    await api.toggleSchedule(id)
    ElMessage.success(isEnabled ? t('schedules.disabled') : t('schedules.enabled'))
    await loadSchedules()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('general.error'))
  }
}

const editSchedule = (schedule: any) => {
  editingSchedule.value = schedule
  form.value = {
    name: schedule.name,
    project_id: schedule.project_id,
    test_plan_id: schedule.test_plan_id || '',
    cron_expression: schedule.cron_expression,
    is_enabled: schedule.is_enabled
  }
  showCreateDialog.value = true
  loadProjectTestPlans(schedule.project_id)
}

const deleteSchedule = async (schedule: any) => {
  try {
    await ElMessageBox.confirm(
      t('schedules.confirmDelete', { name: schedule.name }),
      t('general.confirm'),
      { type: 'warning' }
    )
    await api.deleteSchedule(schedule.id)
    ElMessage.success(t('general.success'))
    await loadSchedules()
  } catch {
    // cancelled
  }
}

const saveSchedule = async () => {
  if (!form.value.name || !form.value.project_id || !form.value.cron_expression) {
    ElMessage.warning(t('schedules.fillRequired'))
    return
  }

  try {
    if (editingSchedule.value) {
      const { project_id, ...patch } = form.value
      await api.updateSchedule(editingSchedule.value.id, patch)
    } else {
      await api.createSchedule(form.value)
    }
    ElMessage.success(editingSchedule.value ? t('general.success') : t('schedules.created'))
    showCreateDialog.value = false
    editingSchedule.value = null
    form.value = {
      name: '',
      project_id: '',
      test_plan_id: '',
      cron_expression: '',
      is_enabled: true
    }
    await loadSchedules()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('general.error'))
  }
}

watch(form, (newVal) => {
  if (newVal.project_id) {
    loadProjectTestPlans(newVal.project_id)
  }
}, { deep: true })

onMounted(async () => {
  await loadProjects()
  await loadSchedules()
})
</script>

<style scoped lang="scss">
.schedules-page {
  max-width: 1400px;
  margin: 0 auto;

  @media (max-width: 768px) {
    padding: 0 16px;
    box-sizing: border-box;
  }
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;

  .header-left {
    min-width: 0;

    h2 {
      margin: 0 0 6px 0;
      font-size: 20px;
      font-weight: 500;
      color: var(--tavily-text-primary);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .subtitle {
      margin: 0;
      font-size: 14px;
      color: var(--tavily-text-muted);
    }
  }

  .el-button {
    width: auto;
  }
}

.main-card {
  border-radius: var(--tavily-radius-lg);
}

.filter-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;

  .filter-select {
    width: 100%;
    max-width: 220px;
  }

  .el-button {
    width: auto;
  }
}

.cron-value {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  font-size: 13px;
  background: var(--tavily-bg-card);
  padding: 4px 8px;
  border-radius: var(--tavily-radius-lg);
  border: 1px solid var(--tavily-border);
}

.cron-hints {
  margin-top: 10px;
  padding: 12px;
  background: var(--tavily-bg-card);
  border-radius: var(--tavily-radius-lg);
  border: 1px solid var(--tavily-border);

  .hint-title {
    font-size: 12px;
    font-weight: 500;
    color: var(--tavily-text-secondary);
    margin-bottom: 8px;
  }

  .hint-item {
    font-size: 12px;
    color: var(--tavily-text-muted);
    font-family: 'SF Mono', 'Cascadia Code', monospace;
    padding: 2px 0;
  }
}

.action-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.table-scroll {
  overflow-x: auto;

  .schedules-table {
    min-width: 700px;
  }
}

@media (max-width: 768px) {
  .col-id,
  .col-plan,
  .col-last,
  .col-count {
    display: none;
  }

  .col-name {
    min-width: 90px;
  }

  .col-project {
    width: 80px;
  }

  .col-cron {
    min-width: 100px;
  }

  .col-next {
    width: 120px;
  }

  .col-status {
    width: 80px;
  }
}
</style>