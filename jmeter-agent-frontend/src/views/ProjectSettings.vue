<template>
  <div class="project-settings">
    <div class="page-header">
      <el-button @click="goBack" text>
        <el-icon><ArrowLeft /></el-icon> {{ t('projectSettings.back') }}
      </el-button>
      <h3>{{ t('projectSettings.title') }}</h3>
      <el-tag v-if="projectAccess" :type="projectAccess.mode === 'private' ? 'info' : 'warning'">
        {{ projectAccess.mode === 'private' ? aclText('私有项目', 'Private project') : aclText('旧共享项目', 'Legacy shared project') }}
      </el-tag>
    </div>
    <el-alert v-if="!canWrite" class="access-notice" :closable="false" type="info"
      :title="accessError || (projectAccess ? aclText('当前只读；项目授权与全局角色共同决定可用操作。', 'Read only. Project access is limited by your global role.') : aclText('正在核对权限，暂时只读。', 'Checking permissions. Read only until verified.'))" />
    <el-button v-if="accessError" text @click="loadSettings">{{ aclText('重新读取权限', 'Retry permission check') }}</el-button>

    <el-tabs v-model="activeSettingsTab" class="settings-tabs">
      <!-- Basic Info Tab -->
      <el-tab-pane :label="t('projectSettings.tab.basic')" name="info">
        <el-card shadow="never" class="info-card">
          <el-form :model="projectForm" :disabled="!canWrite" label-position="top" style="max-width: 560px">
            <el-form-item :label="t('projectSettings.form.name')" required>
              <el-input v-model="projectForm.name" :placeholder="t('projectSettings.form.namePlaceholder')" />
            </el-form-item>
            <el-form-item :label="t('projectSettings.form.description')">
              <el-input v-model="projectForm.description" type="textarea" :rows="3" :placeholder="t('projectSettings.form.descriptionPlaceholder')" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveProjectInfo" :disabled="!canWrite" :loading="savingInfo">
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
          <el-button type="primary" size="small" :disabled="!canWrite" @click="showAddEnvDialog">
            <el-icon><Plus /></el-icon> {{ t('projectSettings.env.add') }}
          </el-button>
        </div>

        <div v-if="environments.length === 0" class="env-empty">
          <el-empty :description="t('projectSettings.env.empty')" :image-size="60">
            <el-button type="primary" :disabled="!canWrite" @click="showAddEnvDialog">{{ t('projectSettings.env.createFirst') }}</el-button>
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
                    :disabled="!canWrite"
                    @click="setActive(env.id)"
                  >
                    {{ t('projectSettings.env.setActive') }}
                  </el-button>
                  <el-button size="small" text :disabled="!canWrite" @click="editEnv(env)">
                    <el-icon><Edit /></el-icon>
                  </el-button>
                  <el-button size="small" text :disabled="!canWrite" @click="duplicateEnv(env)">
                    <el-icon><CopyDocument /></el-icon>
                  </el-button>
                  <el-button size="small" text type="danger" @click="deleteEnv(env)" :disabled="!canWrite || environments.length <= 1">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                </div>
              </div>
            </template>

            <el-form label-position="top" size="small" :disabled="!canWrite">
              <el-form-item label="Base URL">
                <el-input v-model="env.base_url" placeholder="https://api.example.com" @change="updateEnv(env)" />
              </el-form-item>
            </el-form>

            <el-divider>{{ t('projectSettings.env.variables') }}</el-divider>
            <div class="env-table-wrapper">
              <el-table :data="env.varRows || []" border size="small">
                <el-table-column :label="t('projectSettings.env.varName')" min-width="150">
                  <template #default="{ row }">
                    <el-input v-model="row.key" size="small" :disabled="!canWrite" :placeholder="t('projectSettings.env.varNamePlaceholder')" @change="applyEnvVars(env)" />
                  </template>
                </el-table-column>
                <el-table-column :label="t('projectSettings.env.varValue')" min-width="200">
                  <template #default="{ row }">
                    <el-input v-model="row.value" size="small" :disabled="!canWrite" :placeholder="t('projectSettings.env.varValuePlaceholder')" @change="applyEnvVars(env)" />
                  </template>
                </el-table-column>
                <el-table-column width="60">
                  <template #default="{ $index }">
                    <el-button text size="small" type="danger" :disabled="!canWrite" @click="removeEnvVar(env, $index)">
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <el-button text size="small" type="primary" :disabled="!canWrite" @click="addEnvVar(env)" style="margin-top: 6px">
              <el-icon><Plus /></el-icon> {{ t('projectSettings.env.addVar') }}
            </el-button>

            <el-divider>{{ t('projectSettings.env.headers') }}</el-divider>
            <div class="env-table-wrapper">
              <el-table :data="env.headerRows || []" border size="small">
                <el-table-column :label="t('projectSettings.env.headerName')" min-width="150">
                  <template #default="{ row }">
                    <el-input v-model="row.key" size="small" :disabled="!canWrite" :placeholder="t('projectSettings.env.headerNamePlaceholder')" @change="applyEnvHeaders(env)" />
                  </template>
                </el-table-column>
                <el-table-column :label="t('projectSettings.env.headerValue')" min-width="200">
                  <template #default="{ row }">
                    <el-input v-model="row.value" size="small" :disabled="!canWrite" :placeholder="t('projectSettings.env.headerValuePlaceholder')" @change="applyEnvHeaders(env)" />
                  </template>
                </el-table-column>
                <el-table-column width="60">
                  <template #default="{ $index }">
                    <el-button text size="small" type="danger" :disabled="!canWrite" @click="removeEnvHeader(env, $index)">
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <el-button text size="small" type="primary" :disabled="!canWrite" @click="addEnvHeader(env)" style="margin-top: 6px">
              <el-icon><Plus /></el-icon> {{ t('projectSettings.env.addHeader') }}
            </el-button>
          </el-card>
        </div>
      </el-tab-pane>
      <el-tab-pane :label="aclText('访问权限', 'Project access')" name="access">
        <el-card shadow="never" class="info-card">
          <p class="access-help">{{ aclText('旧共享项目保留工作区权限；私有项目仅所有者和成员可读。管理员管理权限不自动授予内容读取权限。', 'Legacy projects retain workspace access. Private content is limited to owners and members; administrator management does not grant content access.') }}</p>
          <template v-if="canManage">
            <el-form label-position="top" :disabled="accessBusy" class="access-form">
              <el-form-item :label="aclText('所有者用户 ID', 'Owner user ID')" required>
                <el-input-number v-model="accessOwner" :min="1" :precision="0" :controls="false" />
              </el-form-item>
              <el-form-item :label="aclText('成员（稳定用户 ID）', 'Members (stable user IDs)')">
                <div class="access-members">
                  <div v-for="(member, index) in accessMembers" :key="index" class="access-member-row">
                    <el-input-number v-model="member.user_id" :min="1" :precision="0" :controls="false"
                      :aria-label="aclText('成员用户 ID', 'Member user ID')" />
                    <el-select v-model="member.role" :aria-label="aclText('成员角色', 'Member role')">
                      <el-option :label="aclText('查看者', 'Viewer')" value="viewer" />
                      <el-option :label="aclText('操作者', 'Operator')" value="operator" />
                    </el-select>
                    <el-button text type="danger" @click="accessMembers.splice(index, 1)">{{ aclText('移除', 'Remove') }}</el-button>
                  </div>
                  <el-button text @click="accessMembers.push({ user_id: undefined, role: 'viewer' })">{{ aclText('添加成员 ID', 'Add member ID') }}</el-button>
                </div>
              </el-form-item>
              <p class="access-help">{{ aclText('仅接受活动账户的用户 ID；不会展示全局账户目录。项目角色不能提升用户的全局权限。', 'Use active account IDs. The global account directory is not exposed; project roles cannot exceed global permissions.') }}</p>
              <el-button type="primary" :loading="accessBusy" @click="previewAccess">{{ aclText('预览权限变更', 'Preview access changes') }}</el-button>
            </el-form>
            <section v-if="accessPreview" class="access-preview" aria-live="polite">
              <h4>{{ aclText('确认前预览', 'Review before applying') }}</h4>
              <p>{{ aclText('访问模式', 'Access mode') }}: {{ accessPreview.current.mode }} → private</p>
              <p>{{ aclText('所有者 ID', 'Owner ID') }}: {{ accessPreview.current.owner_id ?? '—' }} → {{ accessPreview.proposed.owner_id }}</p>
              <p>{{ aclText('原成员', 'Current members') }}: {{ Object.entries(accessPreview.current.members).map(([id, role]) => `${id}: ${role}`).join(', ') || '—' }}</p>
              <p>{{ aclText('变更后成员', 'Proposed members') }}: {{ Object.entries(accessPreview.proposed.members).map(([id, role]) => `${id}: ${role}`).join(', ') || '—' }}</p>
              <dl class="access-counts">
                <template v-for="(count, kind) in accessPreview.resource_counts" :key="kind">
                  <dt>{{ ({ test_plans: aclText('测试计划', 'Test plans'), executions: aclText('执行记录', 'Executions'), baselines: aclText('基线', 'Baselines'), schedules: aclText('定时任务', 'Schedules') } as Record<string, string>)[kind] || kind }}</dt><dd>{{ count }}</dd>
                </template>
              </dl>
              <el-button type="warning" :loading="accessBusy" @click="applyAccess">{{ aclText('确认应用此预览', 'Confirm this preview') }}</el-button>
            </section>
          </template>
          <p v-else class="access-help">{{ aclText('当前账户没有成员管理权限。', 'This account cannot manage project members.') }}</p>
        </el-card>
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
      <el-form :model="envDialogForm" label-position="top" :disabled="!canWrite">
        <el-form-item :label="t('projectSettings.env.name')" required>
          <el-input v-model="envDialogForm.name" :placeholder="t('projectSettings.env.namePlaceholder')" />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="envDialogForm.base_url" placeholder="https://api.example.com" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="envDialogVisible = false">{{ t('projectSettings.cancel') }}</el-button>
        <el-button type="primary" :disabled="!canWrite" @click="submitEnvDialog" :loading="savingEnv">
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
import { api, type ProjectAccess, type ProjectAccessChange, type ProjectAccessPreview, type ProjectMemberRole } from '@/api'
import { useI18n } from '@/composables/useI18n'

const { t, currentLang } = useI18n()
const aclText = (zh: string, en: string) => currentLang.value === 'en' ? en : zh

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

const activeSettingsTab = ref('info')
const savingInfo = ref(false)
const savingEnv = ref(false)
const projectAccess = ref<ProjectAccess | null>(null)
const canRead = computed(() => projectAccess.value?.permissions.read === true)
const canWrite = computed(() => projectAccess.value?.permissions.write === true)
const canManage = computed(() => projectAccess.value?.permissions.manage === true)
const accessError = ref('')
const accessBusy = ref(false)
const accessOwner = ref<number | undefined>()
const accessMembers = ref<{ user_id: number | undefined; role: ProjectMemberRole }[]>([])
const accessPreview = ref<ProjectAccessPreview | null>(null)
watch([accessOwner, accessMembers], () => { accessPreview.value = null }, { deep: true })

function buildAccessChange(): ProjectAccessChange {
  const owner = accessOwner.value
  if (!owner || !Number.isSafeInteger(owner) || owner < 1) {
    throw new Error(aclText('请输入有效的所有者用户 ID', 'Enter a valid owner user ID'))
  }
  const members: Record<string, ProjectMemberRole> = {}
  for (const row of accessMembers.value) {
    if (!row.user_id || !Number.isSafeInteger(row.user_id) || row.user_id < 1 || row.user_id === owner || members[String(row.user_id)]) {
      throw new Error(aclText('成员 ID 必须有效、唯一，且不能重复所有者', 'Member IDs must be valid, unique and different from the owner'))
    }
    members[String(row.user_id)] = row.role
  }
  return { owner_id: owner, members }
}

async function previewAccess() {
  if (!canManage.value || accessBusy.value) return
  const id = projectId.value
  accessBusy.value = true
  accessPreview.value = null
  try {
    const response = await api.previewProjectAccess(id, buildAccessChange())
    if (id === projectId.value) accessPreview.value = response.data
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || error.message || aclText('无法预览权限变更', 'Could not preview access changes'))
  } finally { accessBusy.value = false }
}

async function applyAccess() {
  const preview = accessPreview.value
  if (!canManage.value || !preview || accessBusy.value) return
  const id = projectId.value
  accessBusy.value = true
  try {
    await ElMessageBox.confirm(
      aclText('确认应用下方预览？被移除或降权的成员将在下一次受保护操作前失去相应权限。',
        'Apply the preview below? Removed or downgraded members lose access before their next protected operation.'),
      aclText('确认项目权限变更', 'Confirm project access changes'),
      { type: 'warning', confirmButtonText: aclText('确认应用', 'Apply changes'), cancelButtonText: t('projectSettings.cancel') })
    if (id !== projectId.value || !canManage.value || accessPreview.value !== preview) return
    await api.applyProjectAccess(id, { owner_id: preview.proposed.owner_id,
      members: { ...preview.proposed.members }, expected_revision: preview.revision })
    accessPreview.value = null
    ElMessage.success(aclText('项目权限已更新', 'Project access updated'))
    await loadAccess()
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') {
      accessPreview.value = null
      ElMessage.error(error.response?.data?.detail || aclText('提交失败，请重新预览', 'Apply failed; preview the changes again'))
    }
  } finally { accessBusy.value = false }
}

async function loadAccess() {
  const id = projectId.value
  projectAccess.value = null
  accessPreview.value = null
  accessError.value = ''
  try {
    const response = await api.getProjectAccess(id)
    if (id !== projectId.value) return false
    projectAccess.value = response.data
    accessOwner.value = response.data.owner_id ?? undefined
    accessMembers.value = Object.entries(response.data.members || {}).map(([userId, role]) => ({ user_id: Number(userId), role }))
    if (!response.data.permissions.read && response.data.permissions.manage) activeSettingsTab.value = 'access'
    return true
  } catch {
    if (id === projectId.value) accessError.value = aclText('无法读取权限，当前只读。请重试或联系项目所有者。',
      'Access could not be verified. Read only; retry or contact the project owner.')
    return false
  }
}

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
    ElMessage.error(aclText('变量名不能重复', 'Variable names must be unique'))
    return
  }
  env.variables = rec
  updateEnv(env)
}

function applyEnvHeaders(env: EnvData) {
  const rec = rowsToRecord(env.headerRows || [])
  if (rec === null) {
    ElMessage.error(aclText('请求头名称不能重复', 'Header names must be unique'))
    return
  }
  env.headers = rec
  updateEnv(env)
}

function addEnvVar(env: EnvData) {
  if (!canWrite.value) return
  if (!env.varRows) env.varRows = []
  env.varRows.push({ key: '', value: '' })
}

function removeEnvVar(env: EnvData, index: number) {
  if (!canWrite.value) return
  env.varRows?.splice(index, 1)
  applyEnvVars(env)
}

function addEnvHeader(env: EnvData) {
  if (!canWrite.value) return
  if (!env.headerRows) env.headerRows = []
  env.headerRows.push({ key: '', value: '' })
}

function removeEnvHeader(env: EnvData, index: number) {
  if (!canWrite.value) return
  env.headerRows?.splice(index, 1)
  applyEnvHeaders(env)
}

// Load data
async function loadSettings() {
  projectAccess.value = null
  projectForm.name = ''
  projectForm.description = ''
  environments.value = []
  activeEnvId.value = null
  envDialogVisible.value = false
  if (await loadAccess() && canRead.value) {
    await Promise.all([loadProject(), loadEnvironments()])
  }
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
  if (!canWrite.value) return
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
  if (!canWrite.value) return
  editingEnvId.value = null
  envDialogForm.name = ''
  envDialogForm.base_url = ''
  envDialogVisible.value = true
}

function editEnv(env: EnvData) {
  if (!canWrite.value) return
  editingEnvId.value = env.id
  envDialogForm.name = env.name
  envDialogForm.base_url = env.base_url
  envDialogVisible.value = true
}

async function submitEnvDialog() {
  if (!canWrite.value) return
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
  if (!canWrite.value) return
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
  if (!canWrite.value) return
  try {
    await api.setActiveEnvironment(projectId.value, envId)
    activeEnvId.value = envId
    ElMessage.success(t('projectSettings.message.envSwitched'))
  } catch (e: any) {
    ElMessage.error(t('projectSettings.message.switchFailed'))
  }
}

async function duplicateEnv(env: EnvData) {
  if (!canWrite.value) return
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
  if (!canWrite.value) return
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
    if (id !== projectId.value || !canWrite.value) return
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

  .access-notice { margin-bottom: 16px; }
  .access-help { color: var(--tavily-text-secondary); line-height: 1.7; margin: 0 0 18px; }
  .access-form { max-width: 680px; }
  .access-members { width: 100%; }
  .access-member-row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }
  .access-member-row .el-select { width: 150px; }
  .access-preview { margin-top: 24px; border-top: 1px solid var(--tavily-border); padding-top: 18px; overflow-wrap: anywhere; }
  .access-preview h4 { margin: 0 0 16px; font-size: 16px; }
  .access-counts { display: grid; grid-template-columns: minmax(120px, 1fr) auto; gap: 8px 20px; max-width: 320px; margin: 18px 0; }
  .access-counts dd { margin: 0; font-variant-numeric: tabular-nums; }

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
