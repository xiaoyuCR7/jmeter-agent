<template>
  <div class="test-plans-page">
    <div class="apifox-layout">
      <!-- Left: Resource Tree -->
      <div class="resource-tree">
        <div class="tree-toolbar">
          <el-input
            v-model="searchKeyword"
            :placeholder="t('testPlans.searchPlaceholder')"
            size="small"
            clearable
            :prefix-icon="Search"
          />
          <el-button text size="small" type="primary" @click="openCreateProject" :title="t('testPlans.newProject')">
            <el-icon><Plus /></el-icon>
          </el-button>
        </div>

        <div class="tree-body" v-loading="loadingProjects">
          <template v-if="filteredProjects.length > 0">
            <div
              v-for="project in filteredProjects"
              :key="project.id"
              class="tree-project"
            >
              <div
                :class="['tree-node', 'project-node', { active: selectedProjectId === project.id && !selectedPlanId }]"
                @click="toggleProject(project.id)"
              >
                <el-icon class="expand-icon" :class="{ expanded: expandedProjects.has(project.id) }">
                  <ArrowRight />
                </el-icon>
                <el-icon class="node-icon"><Folder /></el-icon>
                <span class="node-label">{{ project.name }}</span>
                <span class="node-count">{{ project.test_plan_count || 0 }}</span>
                <el-dropdown trigger="click" @command="(cmd: string) => handleProjectAction(cmd, project)">
                  <el-button text size="small" class="node-more" @click.stop>
                    <el-icon><MoreFilled /></el-icon>
                  </el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="edit">
                        <el-icon><Edit /></el-icon> {{ t('testPlans.editProject') }}
                      </el-dropdown-item>
                      <el-dropdown-item command="delete" divided>
                        <el-icon><Delete /></el-icon> {{ t('testPlans.deleteProject') }}
                      </el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>

              <div v-show="expandedProjects.has(project.id)" class="tree-children" v-loading="loadingPlans && selectedProjectId === project.id">
                <div
                  v-for="plan in getProjectPlans(project.id)"
                  :key="plan.id"
                  :class="['tree-node', 'plan-node', { active: selectedPlanId === plan.id }]"
                  @click="selectTestPlan(project.id, plan)"
                >
                  <el-icon class="node-icon plan-icon"><Document /></el-icon>
                  <span class="node-label">{{ plan.name }}</span>
                  <el-tag size="small" class="plan-type-tag">{{ typeText(plan.test_config?.test_type) }}</el-tag>
                </div>
                <div
                  v-if="selectedProjectId === project.id && !loadingPlans && testPlans.length === 0"
                  class="tree-empty-hint"
                >
                  {{ t('testPlans.noTestCases') }}
                </div>
                <div
                  v-if="selectedProjectId === project.id"
                  class="tree-add-plan"
                  @click="goCreateTestPlan"
                >
                  <el-icon><Plus /></el-icon>
                  <span>{{ t('testPlans.newTestCase') }}</span>
                </div>
              </div>
            </div>
          </template>

          <el-empty v-else-if="!loadingProjects" :description="t('testPlans.noProjects')" :image-size="60">
          <el-button type="primary" size="small" @click="openCreateProject">{{ t('testPlans.createProject') }}</el-button>
        </el-empty>
        </div>
      </div>

      <!-- Right: Workspace -->
      <div class="main-workspace">
        <template v-if="!selectedProjectId">
          <div class="workspace-empty">
            <el-empty :description="t('testPlans.selectOrCreate')" :image-size="80">
              <el-button type="primary" @click="openCreateProject">{{ t('testPlans.createProject') }}</el-button>
            </el-empty>
          </div>
        </template>

        <template v-else-if="!selectedPlanId">
          <div class="workspace-overview">
            <div class="overview-header">
              <div class="overview-title">
                <h3>{{ selectedProject?.name }}</h3>
                <el-tag v-if="selectedProject?.environment?.base_url" size="small" type="info">
                  {{ selectedProject?.environment?.base_url }}
                </el-tag>
              </div>
              <div class="overview-actions">
                <el-button @click="goToProjectSettings">
                  <el-icon><Setting /></el-icon> {{ t('testPlans.projectSettings') }}
                </el-button>
                <el-button type="primary" @click="goCreateTestPlan">
                  <el-icon><Plus /></el-icon> {{ t('testPlans.newTestCase') }}
                </el-button>
              </div>
            </div>
            <p v-if="selectedProject?.description" class="overview-desc">{{ selectedProject.description }}</p>
            <div class="overview-stats">
              <div class="stat-card">
                  <div class="stat-value">{{ testPlans.length }}</div>
                  <div class="stat-label">{{ t('testPlans.stat.testCases') }}</div>
                </div>
                <div class="stat-card" v-if="selectedProject?.environment?.variables">
                  <div class="stat-value">{{ Object.keys(selectedProject.environment.variables).length }}</div>
                  <div class="stat-label">{{ t('testPlans.stat.variables') }}</div>
                </div>
            </div>
            <div class="overview-hint">
              <el-icon><InfoFilled /></el-icon>
              <span>{{ t('testPlans.selectOrCreateHint') }}</span>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="workspace-plan">
            <div class="plan-header">
              <div class="plan-title-area">
                <h3>{{ selectedPlan?.name }}</h3>
                <el-tag size="small">{{ typeText(selectedPlan?.test_config?.test_type) }}</el-tag>
                <span class="plan-meta">{{ t('testPlans.endpointCount', { count: selectedPlan?.endpoints_count || 0 }) }}</span>
              </div>
              <div class="plan-actions">
                <el-button @click="openEditor(selectedPlanId!)">
                  <el-icon><Edit /></el-icon> {{ t('testPlans.edit') }}
                </el-button>
                <el-button type="primary" @click="execute(selectedPlanId!)">
                  <el-icon><VideoPlay /></el-icon> {{ t('testPlans.run') }}
                </el-button>
                <el-dropdown trigger="click" @command="(cmd: string) => handlePlanAction(cmd)">
                  <el-button>
                    <el-icon><MoreFilled /></el-icon>
                  </el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="download">{{ t('testPlans.downloadJmx') }}</el-dropdown-item>
                      <el-dropdown-item command="delete" divided>{{ t('testPlans.delete') }}</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </div>
            <p v-if="selectedPlan?.description" class="plan-desc">{{ selectedPlan.description }}</p>

            <!-- Apifox-style quick tabs -->
            <div class="plan-view-tabs">
              <span
                v-for="tab in planViewTabs"
                :key="tab.name"
                :class="['plan-view-tab', { active: planViewTab === tab.name }]"
                @click="planViewTab = tab.name"
              >
                {{ tab.label }}
              </span>
            </div>

            <div class="plan-view-content">
              <div v-if="planViewTab === 'overview'" class="plan-overview-panel">
                <el-descriptions :column="2" border size="small">
                  <el-descriptions-item :label="t('testPlans.label.id')">{{ selectedPlan?.id }}</el-descriptions-item>
                  <el-descriptions-item :label="t('testPlans.label.testType')">{{ typeText(selectedPlan?.test_config?.test_type) }}</el-descriptions-item>
                  <el-descriptions-item :label="t('testPlans.label.endpointCount')">{{ selectedPlan?.endpoints_count || 0 }}</el-descriptions-item>
                  <el-descriptions-item :label="t('testPlans.label.createTime')">{{ formatTime(selectedPlan?.created_at) }}</el-descriptions-item>
                </el-descriptions>
                <div class="plan-cta">
                  <el-button type="primary" size="large" @click="openEditor(selectedPlanId!)">
                    {{ t('testPlans.enterEditor') }}
                  </el-button>
                  <p class="cta-hint">{{ t('testPlans.debugHint') }}</p>
                </div>
              </div>
              <div v-else-if="planViewTab === 'debug'" class="plan-debug-panel">
                <el-empty :description="t('testPlans.debugViewHint')" :image-size="64">
                  <el-button type="primary" @click="openEditor(selectedPlanId!)">{{ t('testPlans.openDebugView') }}</el-button>
                </el-empty>
              </div>
              <div v-else class="plan-testcase-panel">
                <el-empty :description="t('testPlans.testCaseHint')" :image-size="64">
                  <el-button type="primary" @click="openEditor(selectedPlanId!)">{{ t('testPlans.openTestCaseView') }}</el-button>
                </el-empty>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- Create/Edit Project Dialog -->
    <el-dialog
      v-model="projectDialogVisible"
      :title="editingProject ? t('testPlans.editDialog') : t('testPlans.createDialog')"
      width="560px"
      style="max-width: calc(100vw - 32px);"
      destroy-on-close
    >
      <el-form :model="projectForm" label-position="top">
        <el-form-item :label="t('testPlans.projectName')" required>
          <el-input v-model="projectForm.name" :placeholder="t('testPlans.projectNamePlaceholder')" />
        </el-form-item>
        <el-form-item :label="t('testPlans.projectDescription')">
          <el-input v-model="projectForm.description" type="textarea" :rows="2" :placeholder="t('testPlans.projectDescriptionPlaceholder')" />
        </el-form-item>
        <el-divider>{{ t('testPlans.envConfig') }}</el-divider>
        <el-form-item :label="t('testPlans.baseUrl')">
          <el-input v-model="projectForm.environment.base_url" placeholder="https://api.example.com" />
        </el-form-item>
        <el-form-item :label="t('testPlans.envVariables')">
          <div class="kv-editor">
            <div v-for="(value, key) in projectForm.environment.variables" :key="key" class="kv-row">
              <el-input v-model="projectForm.environment.variables[key]" :placeholder="t('testPlans.variableValue')" size="small" />
              <el-button text type="danger" size="small" @click="removeEnvVar(key)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <div class="kv-row">
              <el-input v-model="newEnvVarKey" :placeholder="t('testPlans.variableName')" size="small" />
              <el-input v-model="newEnvVarValue" :placeholder="t('testPlans.variableValue')" size="small" />
              <el-button text type="primary" size="small" @click="addEnvVar">
                <el-icon><Plus /></el-icon>
              </el-button>
            </div>
          </div>
        </el-form-item>
        <el-form-item :label="t('testPlans.globalHeaders')">
          <div class="kv-editor">
            <div v-for="(value, key) in projectForm.environment.headers" :key="key" class="kv-row">
              <el-input v-model="projectForm.environment.headers[key]" :placeholder="t('testPlans.headerValue')" size="small" />
              <el-button text type="danger" size="small" @click="removeHeader(key)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
            <div class="kv-row">
              <el-input v-model="newHeaderKey" :placeholder="t('testPlans.headerName')" size="small" />
              <el-input v-model="newHeaderValue" :placeholder="t('testPlans.headerValue')" size="small" />
              <el-button text type="primary" size="small" @click="addHeader">
                <el-icon><Plus /></el-icon>
              </el-button>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="projectDialogVisible = false">{{ t('testPlans.cancel') }}</el-button>
        <el-button type="primary" @click="submitProject" :loading="submittingProject">
          {{ editingProject ? t('testPlans.save') : t('testPlans.create') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, MoreFilled, Edit, Delete, Setting, Search,
  ArrowRight, Folder, Document, VideoPlay, InfoFilled
} from '@element-plus/icons-vue'
import { api } from '@/api'
import { useI18n } from '@/composables/useI18n'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const projects = ref<any[]>([])
const selectedProjectId = ref<string | null>(null)
const selectedProject = ref<any>(null)
const selectedPlanId = ref<string | null>(null)
const selectedPlan = ref<any>(null)
const testPlans = ref<any[]>([])
const plansCache = ref<Map<string, any[]>>(new Map())
const loadingProjects = ref(false)
const loadingPlans = ref(false)
const searchKeyword = ref('')
const expandedProjects = ref<Set<string>>(new Set())
const planViewTab = ref('overview')

const planViewTabs = [
  { name: 'overview', label: () => t('testPlans.view.overview') },
  { name: 'debug', label: () => t('testPlans.view.debug') },
  { name: 'testcase', label: () => t('testPlans.view.testcase') },
]

// Project dialog
const projectDialogVisible = ref(false)
const editingProject = ref<any>(null)
const submittingProject = ref(false)
const projectForm = ref({
  name: '',
  description: '',
  environment: {
    base_url: '',
    variables: {} as Record<string, string>,
    headers: {} as Record<string, string>
  }
})
const newEnvVarKey = ref('')
const newEnvVarValue = ref('')
const newHeaderKey = ref('')
const newHeaderValue = ref('')

const filteredProjects = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return projects.value
  return projects.value.filter(p =>
    p.name.toLowerCase().includes(kw) ||
    getProjectPlans(p.id).some(plan => plan.name.toLowerCase().includes(kw))
  )
})

function getProjectPlans(projectId: string) {
  if (selectedProjectId.value === projectId) return testPlans.value
  return plansCache.value.get(projectId) || []
}

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

const toggleProject = async (projectId: string) => {
  if (expandedProjects.value.has(projectId)) {
    if (selectedProjectId.value === projectId) {
      expandedProjects.value.delete(projectId)
      return
    }
  }
  expandedProjects.value.add(projectId)
  selectedPlanId.value = null
  selectedPlan.value = null
  await selectProject(projectId)
}

const selectProject = async (projectId: string) => {
  selectedProjectId.value = projectId
  selectedProject.value = projects.value.find(p => p.id === projectId)
  loadingPlans.value = true
  try {
    const res = await api.getProjectTestPlans(projectId)
    testPlans.value = res.data || []
    plansCache.value.set(projectId, testPlans.value)
  } catch {
    testPlans.value = []
  } finally {
    loadingPlans.value = false
  }
}

const selectTestPlan = (projectId: string, plan: any) => {
  if (selectedProjectId.value !== projectId) {
    selectProject(projectId)
  }
  expandedProjects.value.add(projectId)
  selectedPlanId.value = plan.id
  selectedPlan.value = plan
  planViewTab.value = 'overview'
}

const openEditor = (planId: string) => {
  if (selectedProjectId.value) {
    router.push(`/projects/${selectedProjectId.value}/editor/${planId}`)
  }
}

const openCreateProject = () => {
  editingProject.value = null
  projectForm.value = {
    name: '',
    description: '',
    environment: { base_url: '', variables: {}, headers: {} }
  }
  projectDialogVisible.value = true
}

const openEditProject = (project: any) => {
  editingProject.value = project
  projectForm.value = {
    name: project.name,
    description: project.description || '',
    environment: {
      base_url: project.environment?.base_url || '',
      variables: project.environment?.variables || {},
      headers: project.environment?.headers || {}
    }
  }
  projectDialogVisible.value = true
}

const addEnvVar = () => {
  if (!newEnvVarKey.value.trim()) return
  projectForm.value.environment.variables[newEnvVarKey.value.trim()] = newEnvVarValue.value.trim()
  newEnvVarKey.value = ''
  newEnvVarValue.value = ''
}

const removeEnvVar = (key: string) => {
  delete projectForm.value.environment.variables[key]
}

const addHeader = () => {
  if (!newHeaderKey.value.trim()) return
  projectForm.value.environment.headers[newHeaderKey.value.trim()] = newHeaderValue.value.trim()
  newHeaderKey.value = ''
  newHeaderValue.value = ''
}

const removeHeader = (key: string) => {
  delete projectForm.value.environment.headers[key]
}

const submitProject = async () => {
  if (!projectForm.value.name.trim()) {
    ElMessage.warning(t('testPlans.rule.nameRequired'))
    return
  }

  submittingProject.value = true
  try {
    if (editingProject.value) {
      await api.updateProject(editingProject.value.id, {
        name: projectForm.value.name,
        description: projectForm.value.description || null,
        environment: projectForm.value.environment
      })
      ElMessage.success(t('testPlans.message.updated'))
    } else {
      const res = await api.createProject({
        name: projectForm.value.name,
        description: projectForm.value.description || null,
        environment: projectForm.value.environment
      })
      ElMessage.success(t('testPlans.message.created'))
      selectedProjectId.value = res.data?.id || res.data
      expandedProjects.value.add(selectedProjectId.value!)
    }
    projectDialogVisible.value = false
    await loadProjects()
    if (selectedProjectId.value) {
      const updated = projects.value.find(p => p.id === selectedProjectId.value)
      if (updated) {
        selectedProject.value = updated
        await selectProject(selectedProjectId.value)
      }
    }
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('testPlans.message.operationFailed'))
  } finally {
    submittingProject.value = false
  }
}

const handleProjectAction = (cmd: string, project: any) => {
  if (cmd === 'edit') {
    openEditProject(project)
  } else if (cmd === 'delete') {
    deleteProject(project)
  }
}

const handlePlanAction = async (cmd: string) => {
  if (!selectedPlanId.value) return
  if (cmd === 'download') {
    await download(selectedPlanId.value)
  } else if (cmd === 'delete') {
    await remove(selectedPlanId.value)
  }
}

const deleteProject = async (project: any) => {
  try {
    await ElMessageBox.confirm(
      t('testPlans.confirmDeleteProject', { name: project.name }),
      t('testPlans.confirm'),
      { type: 'warning', confirmButtonText: t('testPlans.delete'), cancelButtonText: t('testPlans.cancel') }
    )
    await api.deleteProject(project.id)
    if (selectedProjectId.value === project.id) {
      selectedProjectId.value = null
      selectedProject.value = null
      selectedPlanId.value = null
      selectedPlan.value = null
      testPlans.value = []
    }
    expandedProjects.value.delete(project.id)
    ElMessage.success(t('testPlans.message.projectDeleted'))
    await loadProjects()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(e.response?.data?.detail || t('testPlans.message.deleteFailed'))
    }
  }
}

const goCreateTestPlan = () => {
  if (selectedProjectId.value) {
    router.push(`/projects/${selectedProjectId.value}/test-plans/create`)
  }
}

const goToProjectSettings = () => {
  if (selectedProjectId.value) {
    router.push(`/projects/${selectedProjectId.value}/settings`)
  }
}

const execute = async (planId: string) => {
  try {
    await api.executeTest(planId)
    ElMessage.success(t('testPlans.message.runStarted'))
    router.push(`/executions?projectId=${selectedProjectId.value}`)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('testPlans.message.runFailed'))
  }
}

const download = async (planId: string) => {
  try {
    const res = await api.downloadTestPlan(planId)
    const blob = new Blob([res.data])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `test_plan_${planId}.jmx`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch {
    ElMessage.error(t('testPlans.message.downloadFailed'))
  }
}

const remove = async (planId: string) => {
  try {
    await ElMessageBox.confirm(t('testPlans.confirmDeletePlan'), t('testPlans.prompt'), { type: 'warning' })
    await api.deleteTestPlan(planId)
    ElMessage.success(t('testPlans.message.planDeleted'))
    if (selectedPlanId.value === planId) {
      selectedPlanId.value = null
      selectedPlan.value = null
    }
    if (selectedProjectId.value) {
      await selectProject(selectedProjectId.value)
    }
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(e.response?.data?.detail || t('testPlans.message.planDeleteFailed'))
    }
  }
}

const typeText = (type: string) => {
  const map: Record<string, string> = {
    baseline: t('testPlans.type.benchmark'),
    concurrency: t('testPlans.type.highConcurrency'),
    ramp_up: t('testPlans.type.stepped'),
    stress: t('testPlans.type.stress'),
    stability: t('testPlans.type.stability'),
    spike: t('testPlans.type.spike')
  }
  return map[type] || type || t('testPlans.type.unknown')
}

const formatTime = (time: string) => {
  if (!time) return '-'
  return new Date(time).toLocaleString()
}

onMounted(async () => {
  await loadProjects()

  const queryProjectId = route.query.projectId as string
  const queryPlanId = route.query.planId as string

  if (queryProjectId && projects.value.find(p => p.id === queryProjectId)) {
    expandedProjects.value.add(queryProjectId)
    await selectProject(queryProjectId)
    if (queryPlanId) {
      const plan = testPlans.value.find(p => p.id === queryPlanId)
      if (plan) selectTestPlan(queryProjectId, plan)
    }
  } else if (projects.value.length > 0) {
    expandedProjects.value.add(projects.value[0].id)
    await selectProject(projects.value[0].id)
  }
})
</script>

<style scoped lang="scss">
/* ── Apple-inspired design tokens (local to TestPlans) ── */
.test-plans-page {
  --tp-font-stack: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'Helvetica Neue', Helvetica, Arial, sans-serif;
  --tp-font-mono: 'SF Mono', SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace;

  /* Glass surface opacities tuned for warm cream background */
  --tp-glass-sidebar: rgba(255, 255, 255, 0.52);
  --tp-glass-workspace: rgba(255, 255, 255, 0.68);
  --tp-glass-card: rgba(255, 255, 255, 0.60);
  --tp-glass-card-hover: rgba(255, 255, 255, 0.72);
  --tp-glass-input: rgba(255, 255, 255, 0.45);
  --tp-glass-border: rgba(255, 255, 255, 0.72);
  --tp-glass-border-subtle: rgba(255, 255, 255, 0.40);

  /* Blur / vibrancy tuned for content-heavy panels */
  --tp-blur-sidebar: blur(22px) saturate(160%);
  --tp-blur-workspace: blur(18px) saturate(150%);
  --tp-blur-card: blur(18px) saturate(150%);
  --tp-blur-input: blur(12px) saturate(140%);

  /* Depth / elevation — unified multi-layer soft shadows */
  --tp-shadow-sm: var(--tavily-shadow-sm);
  --tp-shadow-md: var(--tavily-shadow-md);
  --tp-shadow-lg: var(--tavily-shadow-lg);

  /* Shape — unified radius scale */
  --tp-radius-sm: var(--radius-sm);
  --tp-radius-md: var(--radius-md);
  --tp-radius-lg: var(--radius-lg);
  --tp-radius-xl: var(--radius-lg);

  /* Motion */
  --tp-ease-spring: cubic-bezier(0.16, 1, 0.3, 1);
  --tp-ease-smooth: cubic-bezier(0.4, 0, 0.2, 1);
  --tp-duration-fast: 0.15s;
  --tp-duration-normal: 0.25s;
}

html.dark .test-plans-page {
  --tp-glass-sidebar: rgba(40, 40, 44, 0.52);
  --tp-glass-workspace: rgba(40, 40, 44, 0.68);
  --tp-glass-card: rgba(40, 40, 44, 0.60);
  --tp-glass-card-hover: rgba(50, 50, 54, 0.72);
  --tp-glass-input: rgba(255, 255, 255, 0.10);
  --tp-glass-border: rgba(255, 255, 255, 0.14);
  --tp-glass-border-subtle: rgba(255, 255, 255, 0.08);

  --tp-shadow-sm: var(--tavily-shadow-sm);
  --tp-shadow-md: var(--tavily-shadow-md);
  --tp-shadow-lg: var(--tavily-shadow-lg);
}

.kv-editor {
  .kv-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-bottom: 8px;

    .el-input {
      flex: 1;
      min-width: 0;
    }

    .el-button {
      flex-shrink: 0;
      width: auto;
    }
  }
}

.test-plans-page {
  height: 100%;
  min-height: calc(100vh - var(--layout-offset, 132px));
  margin: -16px;
  box-sizing: border-box;
  font-family: var(--tp-font-stack);

  /* Warm cream page background with subtle gradient depth */
  background: var(--tavily-bg-gradient);

  /* Rounded controls following the unified radius scale */
  .el-button {
    border-radius: var(--tp-radius-md);
  }

  .el-button.is-text,
  .el-button.is-link {
    border-radius: var(--tp-radius-md);
  }

  .el-tag {
    border-radius: var(--tp-radius-sm);
  }

  .el-input__wrapper,
  .el-textarea__inner {
    border-radius: var(--tp-radius-sm);
  }

  /* Dialog belonging to this page gets a softer radius */
  :deep(.el-dialog) {
    border-radius: var(--tp-radius-xl);
  }

  @media (min-width: 769px) {
    margin: -20px -24px;
  }

  @media (min-width: 1024px) {
    margin: -24px -28px;
  }
}

.apifox-layout {
  display: flex;
  height: 100%;
  flex-direction: row;

  @media (max-width: 768px) {
    flex-direction: column;
    height: auto;
  }
}

.resource-tree {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 400px;

  /* Apple-style sidebar material */
  background: var(--tp-glass-sidebar);
  backdrop-filter: var(--tp-blur-sidebar);
  -webkit-backdrop-filter: var(--tp-blur-sidebar);
  border-right: 1px solid var(--tp-glass-border);
  box-shadow: inset -1px 0 0 var(--tp-glass-border-subtle);

  @media (max-width: 768px) {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid var(--tp-glass-border);
    box-shadow: inset 0 -1px 0 var(--tp-glass-border-subtle);
    min-height: 200px;
    max-height: 320px;
  }

  @media (min-width: 769px) and (max-width: 1024px) {
    width: 240px;
  }

  .tree-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 14px 14px 10px;
    border-bottom: 1px solid var(--tp-glass-border-subtle);

    .el-input {
      flex: 1;

      :deep(.el-input__wrapper) {
        background: var(--tp-glass-input);
        backdrop-filter: var(--tp-blur-input);
        -webkit-backdrop-filter: var(--tp-blur-input);
        border-radius: var(--tp-radius-md);
      }
    }

    .el-button {
      width: auto;
    }
  }

  .tree-body {
    flex: 1;
    overflow-y: auto;
    padding: 8px 0;
  }

  .tree-node {
    display: flex;
    align-items: center;
    padding: 8px 14px 8px 10px;
    margin: 0 8px;
    cursor: pointer;
    font-size: 13px;
    line-height: 1.4;
    color: var(--tavily-text-primary);
    border-radius: var(--tp-radius-md);
    gap: 8px;
    user-select: none;
    transition:
      background-color var(--tp-duration-fast) var(--tp-ease-smooth),
      color var(--tp-duration-fast) var(--tp-ease-smooth),
      transform var(--tp-duration-fast) var(--tp-ease-spring);

    &:hover {
      background: var(--tavily-primary-bg);
      .node-more { opacity: 1; }
    }

    &.active {
      background: var(--tavily-primary-bg);
      color: var(--tavily-primary);
      font-weight: 600;
      box-shadow: inset 0 0 0 1px rgba(38, 119, 255, 0.12);
    }

    .expand-icon {
      font-size: 12px;
      color: var(--tavily-text-muted);
      transition: transform 0.2s var(--tp-ease-spring);
      flex-shrink: 0;

      &.expanded { transform: rotate(90deg); }
    }

    .node-icon {
      font-size: 15px;
      color: var(--tavily-text-secondary);
      flex-shrink: 0;
    }

    .node-label {
      flex: 1;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      letter-spacing: -0.01em;
    }

    .node-count {
      font-size: 11px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      color: var(--tavily-text-muted);
      background: var(--tp-glass-card);
      border: 1px solid var(--tp-glass-border-subtle);
      padding: 0 6px;
      border-radius: var(--tp-radius-sm);
      line-height: 18px;
      flex-shrink: 0;
    }

    .node-more {
      opacity: 0;
      transition: opacity 0.12s;
      padding: 2px;
      width: auto;

      @media (hover: none) and (pointer: coarse) {
        opacity: 1;
      }
    }
  }

  .tree-children {
    .plan-node {
      padding-left: 38px;

      .plan-icon { color: var(--tavily-primary); }

      .plan-type-tag {
        transform: scale(0.88);
        flex-shrink: 0;
      }
    }

    .tree-empty-hint {
      padding: 8px 12px 8px 38px;
      font-size: 12px;
      color: var(--tavily-text-muted);
    }

    .tree-add-plan {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 14px 8px 38px;
      margin: 0 8px;
      border-radius: var(--tp-radius-md);
      font-size: 12px;
      font-weight: 500;
      color: var(--tavily-primary);
      cursor: pointer;
      transition: background-color var(--tp-duration-fast) var(--tp-ease-smooth);

      &:hover { background: var(--tavily-primary-bg); }
    }
  }
}

.main-workspace {
  flex: 1;
  overflow-y: auto;
  min-width: 0;
  min-height: 500px;

  /* Apple-style workspace material: slightly more opaque for content legibility */
  background: var(--tp-glass-workspace);
  backdrop-filter: var(--tp-blur-workspace);
  -webkit-backdrop-filter: var(--tp-blur-workspace);

  @media (max-width: 768px) {
    min-height: 400px;
  }

  .workspace-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    padding: 20px;
  }

  .workspace-overview {
    padding: 28px 32px;

    @media (max-width: 768px) {
      padding: 18px;
    }

    @media (min-width: 769px) and (max-width: 1024px) {
      padding: 22px;
    }

    .overview-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 16px;
      flex-wrap: wrap;
      gap: 14px;

      @media (max-width: 768px) {
        flex-direction: column;
        align-items: stretch;
      }

      .overview-title {
        min-width: 0;

        h3 {
          margin: 0 0 10px;
          font-size: 22px;
          font-weight: 600;
          letter-spacing: -0.02em;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;

          @media (max-width: 768px) {
            font-size: 19px;
          }
        }
      }

      .overview-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;

        .el-button { width: auto; }

        @media (max-width: 768px) {
          width: 100%;

          .el-button {
            flex: 1;
            min-width: 120px;
          }
        }
      }
    }

    .overview-desc {
      color: var(--tavily-text-secondary);
      font-size: 14px;
      line-height: 1.6;
      margin: 0 0 28px;
      overflow-wrap: break-word;
      word-break: normal;
    }

    .overview-stats {
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      margin-bottom: 28px;

      .stat-card {
        flex: 0 1 auto;
        min-width: 140px;
        padding: 18px 26px;
        background: var(--tp-glass-card);
        backdrop-filter: var(--tp-blur-card);
        -webkit-backdrop-filter: var(--tp-blur-card);
        border: 1px solid var(--tp-glass-border);
        border-radius: var(--tp-radius-xl);
        box-shadow: var(--tp-shadow-sm);
        transition:
          background-color var(--tp-duration-normal) var(--tp-ease-smooth),
          box-shadow var(--tp-duration-normal) var(--tp-ease-smooth),
          transform var(--tp-duration-normal) var(--tp-ease-spring);

        &:hover {
          background: var(--tp-glass-card-hover);
          box-shadow: var(--tp-shadow-md);
          transform: translateY(-2px);
        }

        .stat-value {
          font-size: 30px;
          font-weight: 700;
          letter-spacing: -0.03em;
          font-variant-numeric: tabular-nums;
          color: var(--tavily-primary);
          line-height: 1.1;
        }

        .stat-label {
          font-size: 12px;
          font-weight: 500;
          color: var(--tavily-text-muted);
          margin-top: 6px;
          letter-spacing: 0.01em;
        }
      }
    }

    .overview-hint {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 14px 18px;
      background: var(--tavily-primary-bg);
      border: 1px solid rgba(38, 119, 255, 0.12);
      border-radius: var(--tp-radius-lg);
      font-size: 13px;
      line-height: 1.5;
      color: var(--tavily-text-secondary);
      overflow-wrap: break-word;
      word-break: normal;

      .el-icon {
        color: var(--tavily-primary);
        flex-shrink: 0;
      }
    }
  }

  .workspace-plan {
    display: flex;
    flex-direction: column;
    height: 100%;

    .plan-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 18px 24px 14px;
      border-bottom: 1px solid var(--tp-glass-border-subtle);
      flex-wrap: wrap;
      gap: 14px;

      @media (max-width: 768px) {
        padding: 14px 18px;
        flex-direction: column;
        align-items: stretch;
      }

      .plan-title-area {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        min-width: 0;

        h3 {
          margin: 0;
          font-size: 19px;
          font-weight: 600;
          letter-spacing: -0.02em;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          min-width: 0;

          @media (max-width: 768px) {
            font-size: 17px;
          }
        }

        .plan-meta {
          font-size: 13px;
          font-weight: 500;
          color: var(--tavily-text-muted);
        }
      }

      .plan-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;

        .el-button { width: auto; }

        @media (max-width: 768px) {
          width: 100%;

          .el-button {
            flex: 1;
            min-width: 80px;
          }
        }
      }
    }

    .plan-desc {
      padding: 0 28px 14px;
      margin: 0;
      font-size: 14px;
      line-height: 1.6;
      color: var(--tavily-text-secondary);
      overflow-wrap: break-word;
      word-break: normal;

      @media (max-width: 768px) {
        padding: 0 18px 12px;
      }
    }

    .plan-view-tabs {
      display: inline-flex;
      flex-wrap: wrap;
      align-self: flex-start;
      margin: 0 28px 16px;
      padding: 4px;
      gap: 2px;

      /* Apple-style floating segmented control */
      background: var(--tp-glass-card);
      backdrop-filter: var(--tp-blur-card);
      -webkit-backdrop-filter: var(--tp-blur-card);
      border: 1px solid var(--tp-glass-border);
      border-radius: var(--tp-radius-lg);
      box-shadow: var(--tp-shadow-sm);

      @media (max-width: 768px) {
        align-self: stretch;
        margin: 0 18px 14px;
      }

      .plan-view-tab {
        padding: 8px 18px;
        font-size: 13px;
        font-weight: 500;
        color: var(--tavily-text-secondary);
        cursor: pointer;
        border-radius: var(--tp-radius-md);
        transition:
          color var(--tp-duration-fast) var(--tp-ease-smooth),
          background-color var(--tp-duration-fast) var(--tp-ease-smooth),
          box-shadow var(--tp-duration-fast) var(--tp-ease-smooth);

        &:hover { color: var(--tavily-primary); }

        &.active {
          color: var(--tavily-primary);
          background: var(--tavily-bg-card);
          box-shadow: var(--tp-shadow-sm);
          font-weight: 600;
        }
      }
    }

    .plan-view-content {
      flex: 1;
      padding: 0 28px 28px;
      overflow-y: auto;

      @media (max-width: 768px) {
        padding: 0 18px 18px;
      }

      .plan-overview-panel {
        .el-descriptions {
          background: var(--tp-glass-card);
          backdrop-filter: var(--tp-blur-card);
          -webkit-backdrop-filter: var(--tp-blur-card);
          border: 1px solid var(--tp-glass-border);
          border-radius: var(--tp-radius-lg);
          padding: 8px;

          :deep(.el-descriptions__body) {
            background: transparent;
          }

          :deep(.el-descriptions__label) {
            font-weight: 500;
            color: var(--tavily-text-secondary);
          }

          :deep(.el-descriptions__content) {
            color: var(--tavily-text-primary);
            font-weight: 500;
          }
        }
      }

      .el-descriptions {
        @media (max-width: 768px) {
          :deep(.el-descriptions__body .el-descriptions__table) {
            display: block;
            width: 100%;
          }

          :deep(.el-descriptions__body .el-descriptions__table tbody) {
            display: block;
            width: 100%;
          }

          :deep(.el-descriptions__body .el-descriptions__table tr) {
            display: block;
            width: 100%;
          }

          :deep(.el-descriptions-item__cell) {
            display: block;
            width: 100%;
            box-sizing: border-box;
          }
        }
      }
    }

    .plan-cta {
      margin-top: 36px;
      padding: 28px;
      text-align: center;
      background: var(--tp-glass-card);
      backdrop-filter: var(--tp-blur-card);
      -webkit-backdrop-filter: var(--tp-blur-card);
      border: 1px solid var(--tp-glass-border);
      border-radius: var(--tp-radius-xl);
      box-shadow: var(--tp-shadow-sm);

      .cta-hint {
        margin-top: 12px;
        font-size: 13px;
        line-height: 1.5;
        color: var(--tavily-text-muted);
        overflow-wrap: break-word;
        word-break: normal;
      }
    }
  }
}

/* Accessibility: reduce transparency when requested */
@media (prefers-reduced-transparency: reduce) {
  .test-plans-page {
    --tp-glass-sidebar: var(--tavily-bg-sidebar);
    --tp-glass-workspace: var(--tavily-bg-card);
    --tp-glass-card: var(--tavily-bg-card);
    --tp-glass-card-hover: var(--tavily-bg-card-hover);
    --tp-glass-input: var(--tavily-bg-input);
  }

  .resource-tree,
  .main-workspace,
  .stat-card,
  .plan-view-tabs,
  .plan-overview-panel .el-descriptions,
  .plan-cta,
  .tree-toolbar .el-input :deep(.el-input__wrapper) {
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
  }
}
</style>
