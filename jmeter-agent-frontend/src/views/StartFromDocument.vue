<template>
  <div class="start-page">
    <header class="intro">
      <h2>{{ L('从文档开始', 'Start from a document') }}</h2>
      <p>{{ L('AI 根据文档生成项目和测试计划，再从中提取性能需求草稿供你核对。', 'AI builds the project and test plans from your document, then drafts performance requirements for you to review.') }}</p>
    </header>

    <ol class="stepper">
      <li v-for="(label, i) in stepLabels" :key="i" :class="{ active: stage === i, done: stage > i }">
        <span class="index">{{ i + 1 }}</span>{{ label }}
      </li>
    </ol>

    <!-- 1. Source -->
    <section v-if="stage === 0" class="panel">
      <el-segmented v-model="source" :options="sourceOptions" class="source-switch" />

      <template v-if="source === 'document'">
        <el-form label-position="top">
          <el-form-item :label="L('接口或需求文档', 'API or requirements document')" required>
            <div class="doc-input">
              <el-upload ref="uploadRef" :auto-upload="false" :limit="1" :show-file-list="false"
                accept=".json,.yaml,.yml,.txt,.md,.swagger" :on-change="handleFileChange">
                <el-button><el-icon><Upload /></el-icon> {{ L('读取文件', 'Load file') }}</el-button>
              </el-upload>
              <span v-if="attachedFile" class="file-chip">
                {{ attachedFile.name }} · {{ formatFileSize(attachedFile.size) }}
                <el-button link size="small" @click="clearAttachedFile"><el-icon><Close /></el-icon></el-button>
              </span>
              <span v-else class="hint">{{ L('支持 OpenAPI/Swagger（JSON、YAML）、Markdown、纯文本，10 MB 以内', 'OpenAPI/Swagger (JSON, YAML), Markdown or plain text, up to 10 MB') }}</span>
            </div>
            <el-input v-model="workflowApiDoc" type="textarea" :rows="10" resize="vertical"
              :placeholder="L('也可以直接粘贴文档内容', 'Or paste the document here')" />
          </el-form-item>
          <el-form-item :label="L('测试目标', 'What to test')" required>
            <el-input v-model="workflowRequirements" type="textarea" :rows="4" resize="vertical"
              :placeholder="L('例如：测试登录和下单接口，500 并发持续 10 分钟，下单 P95 不超过 1 秒，错误率低于 0.1%', 'e.g. Test login and checkout at 500 concurrent users for 10 minutes; checkout P95 within 1 s, error rate below 0.1%')" />
            <div class="hint">{{ L('这里写的指标会和文档一起用来提取性能需求', 'Targets written here are used, with the document, to extract performance requirements') }}</div>
          </el-form-item>
          <el-form-item :label="L('模型', 'Model')">
            <el-select v-model="selectedModel" :placeholder="L('选择模型', 'Choose a model')" class="model-select">
              <el-option v-for="model in models" :key="model.id" :label="model.name" :value="model.id" />
            </el-select>
            <router-link v-if="!models.length" to="/settings" class="hint link">{{ L('还没有可用模型，去设置添加', 'No model yet: add one in Settings') }}</router-link>
          </el-form-item>
        </el-form>
        <div class="actions">
          <el-button type="primary" size="large" :loading="workflowLoading"
            :disabled="!workflowApiDoc.trim() || !workflowRequirements.trim()" @click="startWorkflow">
            {{ L('开始生成', 'Generate') }}
          </el-button>
        </div>
      </template>

      <template v-else>
        <el-form label-position="top" @submit.prevent="importOpenApi">
          <el-form-item :label="L('OpenAPI / Swagger 地址', 'OpenAPI / Swagger URL')" required>
            <el-input v-model="openApiUrl" placeholder="https://api.example.com/openapi.json" />
            <div class="hint">{{ L('按接口直接生成测试计划，不调用 AI；之后可在项目里补充性能需求', 'Builds test plans straight from the spec without AI; add performance requirements in the project afterwards') }}</div>
          </el-form-item>
        </el-form>
        <div class="actions">
          <el-button type="primary" size="large" :loading="importing" :disabled="!openApiUrl.trim()" @click="importOpenApi">
            {{ L('导入', 'Import') }}
          </el-button>
        </div>
      </template>
    </section>

    <!-- 2. Progress -->
    <section v-else-if="stage === 1" class="panel">
      <ul class="progress">
        <li v-for="(step, index) in workflowSteps" :key="index" :class="{ done: step.done, active: step.active, error: step.error }">
          <el-icon v-if="step.error"><CircleCloseFilled /></el-icon>
          <el-icon v-else-if="step.done"><CircleCheckFilled /></el-icon>
          <el-icon v-else class="is-loading"><Loading /></el-icon>
          <span>{{ step.message }}</span>
        </li>
        <li v-if="!workflowSteps.length" class="active"><el-icon class="is-loading"><Loading /></el-icon>{{ L('正在连接…', 'Connecting…') }}</li>
      </ul>
      <div v-if="!workflowRunning" class="actions">
        <el-button @click="resetWorkflow">{{ L('返回修改', 'Back') }}</el-button>
      </div>
    </section>

    <!-- 3. Done -->
    <section v-else class="panel">
      <el-result icon="success" :title="result!.title" :sub-title="result!.detail">
        <template #extra>
          <el-button type="primary" @click="reviewRequirements">{{ L('核对性能需求', 'Review requirements') }}</el-button>
          <el-button @click="router.push(`/projects/${result!.projectId}/plans`)">{{ L('查看测试计划', 'View test plans') }}</el-button>
        </template>
      </el-result>
      <div v-if="workflowResult?.diagnostic_summary" class="diagnostic">
        <h4>{{ L('AI 说明', 'AI notes') }}</h4>
        <div class="markdown" v-html="renderMarkdown(workflowResult.diagnostic_summary)"></div>
      </div>
      <div class="actions"><el-button text @click="resetWorkflow">{{ L('再导入一份', 'Start another') }}</el-button></div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Upload, Close, CircleCheckFilled, CircleCloseFilled, Loading } from '@element-plus/icons-vue'
import type { UploadFile, UploadInstance } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { api } from '@/api'
import { useI18n } from '@/composables/useI18n'

const md = new MarkdownIt()
const router = useRouter()
const { t, currentLang } = useI18n()
const L = (zh: string, en: string) => currentLang.value === 'en' ? en : zh

const models = ref<any[]>([])
const selectedModel = ref('')
const source = ref<'document' | 'openapi'>('document')
const sourceOptions = computed(() => [
  { label: L('上传或粘贴文档', 'Document'), value: 'document' },
  { label: L('OpenAPI 地址', 'OpenAPI URL'), value: 'openapi' },
])
const stepLabels = computed(() => [L('选择来源', 'Source'), L('生成', 'Generate'), L('核对', 'Review')])

// Workflow state
const workflowApiDoc = ref('')
const workflowRequirements = ref('')
const workflowRunning = ref(false)
const workflowLoading = ref(false)
const workflowSteps = ref<any[]>([])
const workflowResult = ref<any>(null)

const openApiUrl = ref('')
const importing = ref(false)
const importResult = ref<any>(null)

const uploadRef = ref<UploadInstance>()
const attachedFile = ref<{ name: string; size: number } | null>(null)

const stage = computed(() => (result.value ? 2 : workflowRunning.value || workflowSteps.value.length ? 1 : 0))
const result = computed(() => {
  if (workflowResult.value?.project_id) {
    return { projectId: workflowResult.value.project_id as string, title: L('项目已生成', 'Project created'), detail: workflowResult.value.message as string }
  }
  if (importResult.value?.success) {
    return {
      projectId: importResult.value.project_id as string,
      title: L('导入完成', 'Import complete'),
      detail: L(`项目「${importResult.value.project_name}」，${importResult.value.endpoints_found} 个接口`,
        `Project "${importResult.value.project_name}", ${importResult.value.endpoints_found} endpoints`),
    }
  }
  return null
})

const renderMarkdown = (content: string) => (content ? md.render(content) : '')

const handleFileChange = (file: UploadFile) => {
  const raw = file.raw
  if (!raw) return
  if (raw.size > 10 * 1024 * 1024) {
    ElMessage.warning(t('aiChat.fileSizeError'))
    return
  }
  const reader = new FileReader()
  reader.onload = (e) => {
    workflowApiDoc.value = e.target?.result as string
    attachedFile.value = { name: raw.name, size: raw.size }
  }
  reader.onerror = () => ElMessage.error(t('aiChat.fileReadError'))
  reader.readAsText(raw)
}

const clearAttachedFile = () => {
  attachedFile.value = null
  uploadRef.value?.clearFiles()
}

const formatFileSize = (bytes: number) => {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB'
}

// Workflow methods
const startWorkflow = async () => {
  if (!workflowApiDoc.value.trim() || !workflowRequirements.value.trim()) {
    ElMessage.warning(t('aiChat.fillRequired'))
    return
  }

  workflowRunning.value = true
  workflowLoading.value = true
  workflowSteps.value = []
  workflowResult.value = null

  try {
    const response = await api.runAIWorkflowStream({
      api_documentation: workflowApiDoc.value,
      testing_requirements: workflowRequirements.value,
      ai_model: selectedModel.value || undefined
    })

    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const reader = response.body?.getReader()
    if (!reader) throw new Error('No reader')

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() || '' // Keep incomplete line in buffer
      if (done && buffer) {
        lines.push(buffer)
        buffer = ''
      }

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))

            // Update progress steps
            const existingIndex = workflowSteps.value.findIndex((s: any) => s.step === data.step)
            const stepObj = {
              step: data.step,
              message: data.message,
              progress: data.progress || 0,
              done: data.step === 'complete' || data.progress >= 100,
              active: data.step !== 'complete' && data.progress < 100 && !data.error,
              error: !!data.error
            }

            if (existingIndex >= 0) {
              workflowSteps.value[existingIndex] = stepObj
            } else {
              // Mark previous steps as done
              workflowSteps.value.forEach((s: any) => {
                if (s.active) s.done = true
                s.active = false
              })
              workflowSteps.value.push(stepObj)
            }

            if (data.step === 'complete') {
              workflowResult.value = {
                message: data.message,
                project_id: data.project_id,
                test_plan_ids: data.test_plan_ids,
                diagnostic_summary: data.diagnostic_summary
              }
              workflowRunning.value = false
            }

            if (data.error) {
              workflowRunning.value = false
              ElMessage.error(data.message)
            }
          } catch { /* skip parse errors */ }
        }
      }
      if (done) {
        if (workflowRunning.value) throw new Error('Workflow stream ended before completion')
        break
      }
    }
  } catch (e: any) {
    ElMessage.error(t('aiChat.workflowFailed', { message: e.message || 'Unknown error' }))
    workflowRunning.value = false
  } finally {
    workflowLoading.value = false
    workflowRunning.value = false
  }
}

async function importOpenApi() {
  if (!openApiUrl.value.trim()) return
  importing.value = true
  try {
    const res = await api.importOpenAPI(openApiUrl.value.trim())
    if (res.data.success) importResult.value = res.data
    else ElMessage.error(res.data.error || L('导入失败', 'Import failed'))
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || L('导入失败', 'Import failed'))
  } finally {
    importing.value = false
  }
}

const resetWorkflow = () => {
  workflowSteps.value = []
  workflowResult.value = null
  workflowRunning.value = false
  importResult.value = null
}

// The requirements tab opens with the user's own text prefilled, ready for "Extract with AI".
function reviewRequirements() {
  const doc = workflowResult.value ? `${workflowRequirements.value.trim()}\n\n${workflowApiDoc.value.trim()}`.trim() : ''
  router.push({ path: `/projects/${result.value!.projectId}/requirements`, state: doc ? { requirementsDoc: doc } : {} })
}

const loadModels = async () => {
  try {
    const res = await api.getModels()
    models.value = res.data
    if (models.value.length > 0) {
      selectedModel.value = models.value[0].id
    }
  } catch {
    models.value = []
  }
}

onMounted(loadModels)
</script>

<style scoped lang="scss">
.start-page { max-width: 820px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }

.intro {
  h2 { margin: 0 0 4px; font-size: 20px; font-weight: 600; color: var(--app-text); }
  p { margin: 0; color: var(--app-text-secondary); }
}

.stepper {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  gap: 24px;
  flex-wrap: wrap;

  li {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--app-text-muted);
    font-size: 13px;
    .index {
      width: 22px;
      height: 22px;
      border-radius: 50%;
      border: 1px solid var(--app-border-strong);
      display: grid;
      place-items: center;
      font-size: 12px;
      font-weight: 600;
    }
    &.active { color: var(--app-text); font-weight: 500; .index { background: var(--app-primary); border-color: var(--app-primary); color: #fff; } }
    &.done .index { background: var(--app-success-soft); border-color: transparent; color: var(--app-success); }
  }
}

.source-switch { margin-bottom: 16px; }
.doc-input { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; width: 100%; margin-bottom: 8px; }
.file-chip { font-size: 13px; color: var(--app-text-secondary); background: var(--app-surface-muted); padding: 2px 4px 2px 10px; border-radius: 999px; }
.model-select { width: 240px; margin-right: 12px; }
.link { color: var(--app-primary); text-decoration: none; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }

.progress {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  li { display: flex; align-items: center; gap: 10px; color: var(--app-text-muted); }
  li.active { color: var(--app-primary); }
  li.done { color: var(--app-text-secondary); .el-icon { color: var(--app-success); } }
  li.error { color: var(--app-danger); }
}

.diagnostic {
  border-top: 1px solid var(--app-border);
  padding-top: 12px;
  h4 { margin: 0 0 8px; font-size: 14px; color: var(--app-text); }
  .markdown { color: var(--app-text-secondary); font-size: 13px; }
}
</style>
