<template>
  <div class="project-settings">
    <div class="page-header">
      <el-button @click="goBack" text>
        <el-icon><ArrowLeft /></el-icon> {{ t('projectSettings.back') }}
      </el-button>
      <h3>{{ t('projectSettings.title') }}</h3>
    </div>

    <el-tabs v-model="activeSettingsTab" class="settings-tabs">
      <!-- Basic Info Tab -->
      <el-tab-pane :label="t('projectSettings.tab.basic')" name="info">
        <el-card shadow="never" class="info-card">
          <el-form :model="projectForm" label-position="top" style="max-width: 560px">
            <el-form-item :label="t('projectSettings.form.name')" required>
              <el-input v-model="projectForm.name" :placeholder="t('projectSettings.form.namePlaceholder')" />
            </el-form-item>
            <el-form-item :label="t('projectSettings.form.description')">
              <el-input v-model="projectForm.description" type="textarea" :rows="3" :placeholder="t('projectSettings.form.descriptionPlaceholder')" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveProjectInfo" :loading="savingInfo">
                {{ t('projectSettings.form.save') }}
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>

      <!-- Environments Tab -->
      <el-tab-pane :label="t('projectSettings.tab.environments')" name="environments">
        <div class="env-header">
          <div class="env-header-left">
            <span class="env-count">{{ t('projectSettings.env.count', { count: environments.length }) }}</span>
          </div>
          <el-button type="primary" size="small" @click="showAddEnvDialog">
            <el-icon><Plus /></el-icon> {{ t('projectSettings.env.add') }}
          </el-button>
        </div>

        <div v-if="environments.length === 0" class="env-empty">
          <el-empty :description="t('projectSettings.env.empty')" :image-size="60">
            <el-button type="primary" @click="showAddEnvDialog">{{ t('projectSettings.env.createFirst') }}</el-button>
          </el-empty>
        </div>

        <div v-else class="env-cards">
          <el-card
            v-for="env in environments"
            :key="env.id"
            :class="['env-card', { active: activeEnvId === env.id }]"
            shadow="never"
          >
            <template #header>
              <div class="env-card-header">
                <div class="env-card-title">
                  <el-icon v-if="activeEnvId === env.id" color="var(--tavily-primary)" size="16">
                    <CircleCheckFilled />
                  </el-icon>
                  <span>{{ env.name }}</span>
                  <el-tag v-if="activeEnvId === env.id" size="small" type="success">{{ t('projectSettings.env.active') }}</el-tag>
                </div>
                <div class="env-card-actions">
                  <el-button
                    v-if="activeEnvId !== env.id"
                    size="small"
                    text
                    type="primary"
                   
                    @click="setActive(env.id)"
                  >
                    {{ t('projectSettings.env.setActive') }}
                  </el-button>
                  <el-button size="small" text @click="editEnv(env)">
                    <el-icon><Edit /></el-icon>
                  </el-button>
                  <el-button size="small" text @click="duplicateEnv(env)">
                    <el-icon><CopyDocument /></el-icon>
                  </el-button>
                  <el-button size="small" text type="danger" @click="deleteEnv(env)" :disabled="environments.length <= 1">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </div>
              </div>
            </template>

            <el-form label-position="top" size="small">
              <el-form-item label="Base URL">
                <el-input v-model="env.base_url" placeholder="https://api.example.com" @change="updateEnv(env)" />
              </el-form-item>
            </el-form>

            <el-divider>{{ t('projectSettings.env.variables') }}</el-divider>
            <div class="env-table-wrapper">
              <el-table :data="env.varRows || []" border size="small">
                <el-table-column :label="t('projectSettings.env.varName')" min-width="150">
                  <template #default="{ row }">
                    <el-input v-model="row.key" size="small" :placeholder="t('projectSettings.env.varNamePlaceholder')" @change="applyEnvVars(env)" />
                  </template>
                </el-table-column>
                <el-table-column :label="t('projectSettings.env.varValue')" min-width="200">
                  <template #default="{ row }">
                    <el-input v-model="row.value" size="small" :placeholder="t('projectSettings.env.varValuePlaceholder')" @change="applyEnvVars(env)" />
                  </template>
                </el-table-column>
                <el-table-column width="60">
                  <template #default="{ $index }">
                    <el-button text size="small" type="danger" @click="removeEnvVar(env, $index)">
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <el-button text size="small" type="primary" @click="addEnvVar(env)" style="margin-top: 6px">
              <el-icon><Plus /></el-icon> {{ t('projectSettings.env.addVar') }}
            </el-button>

            <el-divider>{{ t('projectSettings.env.headers') }}</el-divider>
            <div class="env-table-wrapper">
              <el-table :data="env.headerRows || []" border size="small">
                <el-table-column :label="t('projectSettings.env.headerName')" min-width="150">
                  <template #default="{ row }">
                    <el-input v-model="row.key" size="small" :placeholder="t('projectSettings.env.headerNamePlaceholder')" @change="applyEnvHeaders(env)" />
                  </template>
                </el-table-column>
                <el-table-column :label="t('projectSettings.env.headerValue')" min-width="200">
                  <template #default="{ row }">
                    <el-input v-model="row.value" size="small" :placeholder="t('projectSettings.env.headerValuePlaceholder')" @change="applyEnvHeaders(env)" />
                  </template>
                </el-table-column>
                <el-table-column width="60">
                  <template #default="{ $index }">
                    <el-button text size="small" type="danger" @click="removeEnvHeader(env, $index)">
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <el-button text size="small" type="primary" @click="addEnvHeader(env)" style="margin-top: 6px">
              <el-icon><Plus /></el-icon> {{ t('projectSettings.env.addHeader') }}
            </el-button>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="localText('性能需求', 'Performance requirements')" name="requirements" lazy>
        <RequirementsPanel :project-id="projectId" />
      </el-tab-pane>
    </el-tabs>

    <!-- Add/Edit Environment Dialog -->
    <el-dialog
      v-model="envDialogVisible"
      :title="editingEnvId ? t('projectSettings.env.edit') : t('projectSettings.env.add')"
      width="480px"
      style="max-width: calc(100vw - 32px);"
      destroy-on-close
    >
      <el-form :model="envDialogForm" label-position="top">
        <el-form-item :label="t('projectSettings.env.name')" required>
          <el-input v-model="envDialogForm.name" :placeholder="t('projectSettings.env.namePlaceholder')" />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="envDialogForm.base_url" placeholder="https://api.example.com" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="envDialogVisible = false">{{ t('projectSettings.cancel') }}</el-button>
        <el-button type="primary" @click="submitEnvDialog" :loading="savingEnv">
          {{ editingEnvId ? t('projectSettings.save') : t('projectSettings.create') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft, Plus, Edit, CopyDocument, Delete, CircleCheckFilled
} from '@element-plus/icons-vue'
import { api } from '@/api'
import RequirementsPanel from '@/components/RequirementsPanel.vue'
import { useI18n } from '@/composables/useI18n'

const { t, currentLang } = useI18n()
const localText = (zh: string, en: string) => currentLang.value === 'en' ? en : zh

interface KVRow { key: string; value: string }

interface EnvData {
  id: string
  name: string
  base_url: string
  variables: Record<string, string>
  headers: Record<string, string>
  varRows?: KVRow[]
  headerRows?: KVRow[]
}

const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.params.projectId as string)

const activeSettingsTab = ref(route.query.tab === 'requirements' ? 'requirements' : 'info')
const savingInfo = ref(false)
const savingEnv = ref(false)
// Project form
const projectForm = reactive({
  name: '',
  description: '',
})

// Environments
const environments = ref<EnvData[]>([])
const activeEnvId = ref<string | null>(null)

// Env dialog
const envDialogVisible = ref(false)
const editingEnvId = ref<string | null>(null)
const envDialogForm = reactive({
  name: '',
  base_url: '',
})

// Rebuild a record from table rows; returns null when duplicate keys exist.
function rowsToRecord(rows: KVRow[]): Record<string, string> | null {
  const out = new Map<string, string>()
  for (const r of rows) {
    const k = r.key.trim()
    if (!k) continue
    if (out.has(k)) return null
    out.set(k, r.value)
  }
  return Object.fromEntries(out)
}

function applyEnvVars(env: EnvData) {
  const rec = rowsToRecord(env.varRows || [])
  if (rec === null) {
    ElMessage.error(localText('变量名不能重复', 'Variable names must be unique'))
    return
  }
  env.variables = rec
  updateEnv(env)
}

function applyEnvHeaders(env: EnvData) {
  const rec = rowsToRecord(env.headerRows || [])
  if (rec === null) {
    ElMessage.error(localText('请求头名称不能重复', 'Header names must be unique'))
    return
  }
  env.headers = rec
  updateEnv(env)
}

function addEnvVar(env: EnvData) {
  if (!env.varRows) env.varRows = []
  env.varRows.push({ key: '', value: '' })
}

function removeEnvVar(env: EnvData, index: number) {
  env.varRows?.splice(index, 1)
  applyEnvVars(env)
}

function addEnvHeader(env: EnvData) {
  if (!env.headerRows) env.headerRows = []
  env.headerRows.push({ key: '', value: '' })
}

function removeEnvHeader(env: EnvData, index: number) {
  env.headerRows?.splice(index, 1)
  applyEnvHeaders(env)
}

// Load data
async function loadSettings() {
  projectForm.name = ''
  projectForm.description = ''
  environments.value = []
  activeEnvId.value = null
  envDialogVisible.value = false
  await Promise.all([loadProject(), loadEnvironments()])
}
watch(projectId, loadSettings, { immediate: true })

async function loadProject() {
  const id = projectId.value
  try {
    const res = await api.getProject(id)
    if (id !== projectId.value) return
    projectForm.name = res.data.name || ''
    projectForm.description = res.data.description || ''
  } catch (e: any) {
    ElMessage.error(t('projectSettings.message.loadFailed'))
  }
}

async function loadEnvironments() {
  const id = projectId.value
  try {
    const res = await api.getProjectEnvironments(id)
    if (id !== projectId.value) return
    if (res.data?.environments) {
      environments.value = res.data.environments.map((e: any) => ({
        ...e,
        variables: e.variables || {},
        headers: e.headers || {},
        varRows: Object.entries(e.variables || {}).map(([key, value]) => ({ key, value: String(value) })),
        headerRows: Object.entries(e.headers || {}).map(([key, value]) => ({ key, value: String(value) })),
      }))
    }
    activeEnvId.value = res.data?.active_id || null
  } catch {
    if (id === projectId.value) environments.value = []
  }
}

// Actions
async function saveProjectInfo() {
  savingInfo.value = true
  try {
    await api.updateProject(projectId.value, {
      name: projectForm.name,
      description: projectForm.description || null,
    })
    ElMessage.success(t('projectSettings.message.saveSuccess'))
  } catch (e: any) {
    ElMessage.error(t('projectSettings.message.saveFailed') + ': ' + (e.response?.data?.detail || e.message))
  } finally {
    savingInfo.value = false
  }
}

function showAddEnvDialog() {
  editingEnvId.value = null
  envDialogForm.name = ''
  envDialogForm.base_url = ''
  envDialogVisible.value = true
}

function editEnv(env: EnvData) {
  editingEnvId.value = env.id
  envDialogForm.name = env.name
  envDialogForm.base_url = env.base_url
  envDialogVisible.value = true
}

async function submitEnvDialog() {
  if (!envDialogForm.name.trim()) {
    ElMessage.warning(t('projectSettings.env.nameRequired'))
    return
  }

  savingEnv.value = true
  try {
    if (editingEnvId.value) {
      await api.updateEnvironment(projectId.value, editingEnvId.value, {
        name: envDialogForm.name,
        base_url: envDialogForm.base_url,
      })
      ElMessage.success(t('projectSettings.message.envUpdated'))
    } else {
      await api.createEnvironment(projectId.value, {
        name: envDialogForm.name,
        base_url: envDialogForm.base_url,
        variables: {},
        headers: {},
      })
      ElMessage.success(t('projectSettings.message.envCreated'))
    }
    envDialogVisible.value = false
    await loadEnvironments()
  } catch (e: any) {
    ElMessage.error(t('projectSettings.message.operationFailed') + ': ' + (e.response?.data?.detail || e.message))
  } finally {
    savingEnv.value = false
  }
}

// Auto-saves send the whole record, so they must reach the server in edit order.
const envSaves = new Map<string, Promise<void>>()

function updateEnv(env: EnvData) {
  const id = projectId.value
  const payload = {
    base_url: env.base_url,
    variables: { ...env.variables },
    headers: { ...env.headers },
  }
  const save = (envSaves.get(env.id) || Promise.resolve()).then(async () => {
    try {
      await api.updateEnvironment(id, env.id, payload)
    } catch (e: any) {
      ElMessage.error(t('projectSettings.message.saveFailed') + ': ' + (e.response?.data?.detail || e.message))
    }
  })
  envSaves.set(env.id, save)
  return save
}

async function setActive(envId: string) {
  try {
    await api.setActiveEnvironment(projectId.value, envId)
    activeEnvId.value = envId
    ElMessage.success(t('projectSettings.message.envSwitched'))
  } catch (e: any) {
    ElMessage.error(t('projectSettings.message.switchFailed'))
  }
}

async function duplicateEnv(env: EnvData) {
  try {
    await api.createEnvironment(projectId.value, {
      name: env.name + t('projectSettings.env.copySuffix'),
      base_url: env.base_url,
      variables: env.variables,
      headers: env.headers,
    })
    ElMessage.success(t('projectSettings.message.envCopied'))
    await loadEnvironments()
  } catch (e: any) {
    ElMessage.error(t('projectSettings.message.copyFailed'))
  }
}

async function deleteEnv(env: EnvData) {
  const id = projectId.value
  if (environments.value.length <= 1) {
    ElMessage.warning(t('projectSettings.env.atLeastOne'))
    return
  }
  try {
    await ElMessageBox.confirm(
      t('projectSettings.env.confirmDelete', { name: env.name }),
      t('projectSettings.confirmDelete'),
      { type: 'warning', confirmButtonText: t('projectSettings.delete'), cancelButtonText: t('projectSettings.cancel') }
    )
    if (id !== projectId.value) return
    await api.deleteEnvironment(id, env.id)
    ElMessage.success(t('projectSettings.message.envDeleted'))
    await loadEnvironments()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(t('projectSettings.message.deleteFailed'))
    }
  }
}

function goBack() {
  router.push(`/test-plans?projectId=${projectId.value}`)
}
</script>

<style scoped lang="scss">
.project-settings {
  max-width: 960px;
  margin: 0 auto;
  box-sizing: border-box;


  @media (max-width: 768px) {
    padding: 0 16px;
  }

  .page-header {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 20px;

    h3 {
      margin: 0;
      font-size: 20px;
      font-weight: 500;
      color: var(--tavily-text-primary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
    }
  }

  .settings-tabs {
    :deep(.el-tabs__header) {
      margin-bottom: 20px;
    }
  }

  .info-card {
    border: 1px solid var(--tavily-border);
    border-radius: var(--tavily-radius-xl);
  }

  .env-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;

    .env-count {
      font-size: 14px;
      color: var(--tavily-text-secondary);
    }
  }

  .env-empty {
    padding: 40px 0;
  }

  .env-cards {
    display: flex;
    flex-direction: column;
    gap: 18px;
  }

  .env-card {
    border: 1px solid var(--tavily-border);
    border-radius: var(--tavily-radius-xl);

    &.active {
      border-color: var(--tavily-primary);
      box-shadow: 0 0 0 1px var(--tavily-primary);
    }

    .env-card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;

      .env-card-title {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 15px;
        font-weight: 500;
        color: var(--tavily-text-primary);
        min-width: 0;

        span {
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          min-width: 0;
        }
      }

      .env-card-actions {
        display: flex;
        gap: 8px;

        .el-button { width: auto; }
      }
    }
  }

  .env-table-wrapper {
    overflow-x: auto;
  }
}
</style>
