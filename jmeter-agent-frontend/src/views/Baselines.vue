<template>
  <div class="baselines-page">
    <div class="page-header">
      <div class="header-left">
        <h2>{{ t('baselines.title') }}</h2>
        <p class="subtitle">{{ t('baselines.subtitle') }}</p>
      </div>
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon> {{ t('baselines.create') }}
      </el-button>
    </div>

    <div class="stats-row">
      <el-card shadow="never" class="stat-card">
        <div class="stat-icon baseline-icon"><el-icon size="24"><DataAnalysis /></el-icon></div>
        <div class="stat-content">
          <div class="stat-value">{{ baselines.length }}</div>
          <div class="stat-label">{{ t('baselines.total') }}</div>
        </div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-icon active-icon"><el-icon size="24"><CircleCheck /></el-icon></div>
        <div class="stat-content">
          <div class="stat-value">{{ activeCount }}</div>
          <div class="stat-label">{{ t('baselines.active') }}</div>
        </div>
      </el-card>
    </div>

    <el-card shadow="never" class="main-card">
      <div class="filter-bar">
        <el-select
          v-model="selectedProjectId"
          :placeholder="t('baselines.selectProject')"
          class="filter-select"
          @change="loadBaselines"
        >
          <el-option label="全部项目" value="" />
          <el-option
            v-for="project in projects"
            :key="project.id"
            :label="project.name"
            :value="project.id"
          />
        </el-select>
        <el-button text @click="loadBaselines">
          <el-icon><Refresh /></el-icon> {{ t('general.refresh') }}
        </el-button>
      </div>

      <div class="table-scroll">
      <el-table :data="baselines" v-loading="loading" stripe class="baselines-table">
        <el-table-column prop="id" :label="t('general.id')" width="70" class-name="col-id" />
        <el-table-column prop="name" :label="t('general.name')" min-width="100" show-overflow-tooltip class-name="col-name" />
        <el-table-column prop="project_id" :label="t('baselines.project')" width="100" show-overflow-tooltip class-name="col-project">
          <template #default="{ row }">
            {{ getProjectName(row.project_id) }}
          </template>
        </el-table-column>
        <el-table-column prop="test_plan_id" :label="t('baselines.testPlan')" width="100" show-overflow-tooltip class-name="col-plan">
          <template #default="{ row }">
            {{ row.test_plan_id || t('general.none') }}
          </template>
        </el-table-column>
        <el-table-column prop="description" :label="t('general.description')" min-width="150" show-overflow-tooltip class-name="col-desc">
          <template #default="{ row }">
            {{ row.description || t('general.none') }}
          </template>
        </el-table-column>
        <el-table-column :label="t('baselines.thresholds')" width="180" class-name="col-threshold">
          <template #default="{ row }">
            <div class="thresholds">
              <div v-if="row.thresholds?.p99_max" class="threshold-item">
                <span class="threshold-label">{{ t('baselines.p99Max') }}:</span>
                <span class="threshold-value">{{ row.thresholds.p99_max }}ms</span>
              </div>
              <div v-if="row.thresholds?.error_rate_max" class="threshold-item">
                <span class="threshold-label">{{ t('baselines.errorRateMax') }}:</span>
                <span class="threshold-value">{{ (row.thresholds.error_rate_max * 100).toFixed(1) }}%</span>
              </div>
              <div v-if="row.thresholds?.avg_rt_max" class="threshold-item">
                <span class="threshold-label">{{ t('baselines.avgRtMax') }}:</span>
                <span class="threshold-value">{{ row.thresholds.avg_rt_max }}ms</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('baselines.metrics')" width="120" class-name="col-metrics">
          <template #default="{ row }">
            <div v-if="row.metrics_snapshot && Object.keys(row.metrics_snapshot).length > 0">
              <el-tag size="small" type="info">{{ t('baselines.hasSnapshot') }}</el-tag>
            </div>
            <span v-else class="text-muted">{{ t('general.none') }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('general.status')" width="90" class-name="col-status">
          <template #default="{ row }">
            <el-tag :type="isActive(row) ? 'success' : 'info'">
              {{ isActive(row) ? t('general.enabled') : t('general.disabled') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('general.operation')" min-width="150" class-name="col-actions">
          <template #default="{ row }">
            <div class="action-buttons">
              <el-button
                text
                type="primary"
                size="small"
                v-if="!isActive(row)"
                @click="activateBaseline(row)"
              >
                {{ t('baselines.activate') }}
              </el-button>
              <el-button text type="danger" size="small" @click="deleteBaseline(row)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      </div>

      <el-empty
        v-if="!loading && baselines.length === 0"
        :description="t('baselines.empty')"
        :image-size="60"
      />
    </el-card>

    <el-dialog
      v-model="showCreateDialog"
      :title="t('baselines.createTitle')"
      width="550px"
      style="max-width: calc(100vw - 32px);"
    >
      <el-form :model="form" label-width="120px">
        <el-form-item :label="t('general.name')" required>
          <el-input v-model="form.name" :placeholder="t('baselines.namePlaceholder')" />
        </el-form-item>
        <el-form-item :label="t('baselines.project')" required>
          <el-select v-model="form.project_id" :placeholder="t('baselines.selectProject')">
            <el-option
              v-for="project in projects"
              :key="project.id"
              :label="project.name"
              :value="project.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('baselines.testPlan')">
          <el-select v-model="form.test_plan_id" :placeholder="t('baselines.selectTestPlan')">
            <el-option label="不指定" value="" />
            <el-option
              v-for="plan in projectTestPlans"
              :key="plan.id"
              :label="plan.name"
              :value="plan.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('general.description')">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            :placeholder="t('baselines.descriptionPlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="t('baselines.thresholds')">
          <el-divider content-position="left">{{ t('baselines.p99Max') }}</el-divider>
          <el-input-number
            v-model="form.thresholds.p99_max"
            :min="1"
            :max="60000"
            :step="100"
            :placeholder="t('baselines.p99Placeholder')"
            class="threshold-input"
          />
          <span class="threshold-unit">ms</span>
          <el-divider content-position="left">{{ t('baselines.errorRateMax') }}</el-divider>
          <el-input-number
            v-model="form.thresholds.error_rate_max"
            :min="0"
            :max="1"
            :step="0.001"
            :precision="3"
            :placeholder="t('baselines.errorRatePlaceholder')"
            class="threshold-input"
          />
          <span class="threshold-unit">{{ t('baselines.percent') }}</span>
          <el-divider content-position="left">{{ t('baselines.avgRtMax') }}</el-divider>
          <el-input-number
            v-model="form.thresholds.avg_rt_max"
            :min="1"
            :max="60000"
            :step="50"
            :placeholder="t('baselines.avgRtPlaceholder')"
            class="threshold-input"
          />
          <span class="threshold-unit">ms</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">{{ t('general.cancel') }}</el-button>
        <el-button type="primary" @click="saveBaseline">{{ t('general.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useI18n } from '@/composables/useI18n'
import { Plus, Refresh, Delete, CircleCheck, DataAnalysis } from '@element-plus/icons-vue'

const { t } = useI18n()

const baselines = ref<any[]>([])
const projects = ref<any[]>([])
const projectTestPlans = ref<any[]>([])
const loading = ref(false)
const showCreateDialog = ref(false)
const selectedProjectId = ref('')
const activeCount = computed(() => baselines.value.filter(b => b.is_active).length)

const form = ref({
  name: '',
  project_id: '',
  test_plan_id: '',
  description: '',
  thresholds: {
    p99_max: 2000,
    error_rate_max: 0.01,
    avg_rt_max: 500
  }
})

const loadProjects = async () => {
  try {
    const res = await api.getProjects()
    projects.value = res.data?.projects || res.data || []
  } catch {
    projects.value = []
  }
}

const loadBaselines = async () => {
  loading.value = true
  try {
    const res = await api.getBaselines(selectedProjectId.value || undefined)
    baselines.value = res.data || []
  } catch {
    baselines.value = []
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

const isActive = (baseline: any) => {
  return !!baseline.is_active
}

const activateBaseline = async (baseline: any) => {
  try {
    await api.activateBaseline(baseline.id)
    ElMessage.success(t('baselines.activated'))
    await loadBaselines()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('general.error'))
  }
}

const deleteBaseline = async (baseline: any) => {
  try {
    await ElMessageBox.confirm(
      t('baselines.confirmDelete', { name: baseline.name }),
      t('general.confirm'),
      { type: 'warning' }
    )
    await api.deleteBaseline(baseline.id)
    ElMessage.success(t('general.success'))
    await loadBaselines()
  } catch {
    // cancelled
  }
}

const saveBaseline = async () => {
  if (!form.value.name || !form.value.project_id) {
    ElMessage.warning(t('baselines.fillRequired'))
    return
  }

  try {
    await api.createBaseline({
      project_id: form.value.project_id,
      name: form.value.name,
      test_plan_id: form.value.test_plan_id || undefined,
      description: form.value.description,
      thresholds: form.value.thresholds
    })
    ElMessage.success(t('baselines.created'))
    showCreateDialog.value = false
    form.value = {
      name: '',
      project_id: '',
      test_plan_id: '',
      description: '',
      thresholds: {
        p99_max: 2000,
        error_rate_max: 0.01,
        avg_rt_max: 500
      }
    }
    await loadBaselines()
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
  await loadBaselines()
})
</script>

<style scoped lang="scss">
.baselines-page {
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

.stats-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 20px;

  .stat-card {
    flex: 1 1 180px;
    display: flex;
    align-items: center;
    gap: 16px;
    border-radius: var(--tavily-radius-xl);
    min-width: 180px;

    .stat-icon {
      width: 48px;
      height: 48px;
      border-radius: var(--tavily-radius-md);
      display: flex;
      align-items: center;
      justify-content: center;

      .el-icon {
        color: var(--tavily-text-primary);
      }

      &.baseline-icon {
        background: var(--tavily-primary-bg);
        .el-icon { color: var(--tavily-primary); }
      }

      &.active-icon {
        background: #DCFCE7;
        .el-icon { color: #16A34A; }
      }
    }

    .stat-content {
      .stat-value {
        font-size: 24px;
        font-weight: 600;
        color: var(--tavily-text-primary);
      }

      .stat-label {
        font-size: 13px;
        color: var(--tavily-text-muted);
      }
    }
  }
}

.main-card {
  border-radius: var(--tavily-radius-xl);
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

.thresholds {
  display: flex;
  flex-direction: column;
  gap: 4px;

  .threshold-item {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    min-width: 0;
    font-size: 12px;

    .threshold-label {
      color: var(--tavily-text-muted);
    }

    .threshold-value {
      color: var(--tavily-text-secondary);
      font-family: 'SF Mono', 'Cascadia Code', monospace;
      word-break: break-word;
      overflow-wrap: break-word;
      min-width: 0;
    }
  }
}

.threshold-input {
  width: 150px;
  margin-right: 8px;
}

.threshold-unit {
  font-size: 13px;
  color: var(--tavily-text-muted);
}

.text-muted {
  color: var(--tavily-text-muted);
}

.action-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.table-scroll {
  overflow-x: auto;

  .baselines-table {
    min-width: 700px;
  }
}

@media (max-width: 768px) {
  .col-id,
  .col-plan,
  .col-desc,
  .col-threshold,
  .col-metrics {
    display: none;
  }

  .col-name {
    min-width: 90px;
  }

  .col-project {
    width: 80px;
  }

  .col-status {
    width: 80px;
  }
}
</style>