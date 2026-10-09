<template>
  <div class="ai-workflow-page">
    <div class="page-header">
      <div class="page-title">
        <el-icon><Cpu /></el-icon> {{ t('chat.workflow') }}
      </div>
      <div class="header-right">
        <el-select v-model="selectedModel" :placeholder="t('chat.selectModel')" size="small" class="model-select">
          <el-option
            v-for="model in models"
            :key="model.id"
            :label="model.name"
            :value="model.id"
          />
        </el-select>
      </div>
    </div>

    <!-- AI Workflow -->
    <div class="workflow-container">
      <div class="workflow-sections">
        <!-- Section 1: API Documentation -->
        <div class="workflow-section">
          <div class="section-header">
            <span class="section-number">1</span>
            <span class="section-title">{{ t('workflow.uploadDoc') }}</span>
          </div>
          <div class="section-body">
            <!-- File Upload Area -->
            <div class="upload-area">
              <el-upload
                ref="uploadRef"
                :auto-upload="false"
                :limit="1"
                :accept="'.json,.yaml,.yml,.txt,.md,.swagger'"
                :on-change="handleFileChange"
                :on-remove="handleFileRemove"
                :show-file-list="false"
                drag
              >
                <div class="upload-trigger">
                  <el-icon class="upload-icon"><UploadFilled /></el-icon>
                  <div class="upload-text">
                    <span class="upload-link">{{ t('workflow.clickUpload') }}</span>
                    <span class="upload-or">{{ t('workflow.dragDrop') }}</span>
                  </div>
                  <div class="upload-hint">{{ t('workflow.uploadHint') }}</div>
                </div>
              </el-upload>
              <!-- Attached file info -->
              <div v-if="attachedFile" class="attached-file">
                <el-icon :class="['file-type-icon', getFileIcon(attachedFile.name)]">
                  <Document />
                </el-icon>
                <span class="file-name">{{ attachedFile.name }}</span>
                <span class="file-size">{{ formatFileSize(attachedFile.size) }}</span>
                <el-button type="danger" link size="small" @click="clearAttachedFile">
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
            </div>
            <!-- Textarea -->
            <el-input
              v-model="workflowApiDoc"
              type="textarea"
              :rows="10"
              :placeholder="t('aiChat.docPlaceholder')"
            />
            <div class="section-hint">
              <el-icon><InfoFilled /></el-icon>
              {{ t('aiChat.docPlaceholder2') }}
            </div>
          </div>
        </div>

        <!-- Section 2: Testing Requirements -->
        <div class="workflow-section">
          <div class="section-header">
            <span class="section-number">2</span>
            <span class="section-title">{{ t('workflow.requirements') }}</span>
          </div>
          <div class="section-body">
            <el-input
              v-model="workflowRequirements"
              type="textarea"
              :rows="4"
              :placeholder="t('aiChat.requirementPlaceholder')"
            />
          </div>
        </div>

        <!-- Section 3: Progress & Results -->
        <div class="workflow-section" v-if="workflowRunning || workflowResult">
          <div class="section-header">
            <span class="section-number">3</span>
            <span class="section-title">{{ t('workflow.progress') }}</span>
          </div>
          <div class="section-body">
            <!-- Progress Steps -->
            <div class="progress-steps" v-if="workflowRunning || workflowSteps.length > 0">
              <div
                v-for="(step, index) in workflowSteps"
                :key="index"
                :class="['progress-step', { done: step.done, active: step.active, error: step.error }]"
              >
                <div class="step-indicator">
                  <el-icon v-if="step.error"><CircleCloseFilled /></el-icon>
                  <el-icon v-else-if="step.done"><CircleCheckFilled /></el-icon>
                  <el-icon v-else-if="step.active" class="is-loading"><Loading /></el-icon>
                  <span v-else class="step-dot"></span>
                </div>
                <div class="step-content">
                  <div class="step-message">{{ step.message }}</div>
                </div>
              </div>
            </div>

            <!-- Result -->
            <div v-if="workflowResult" class="workflow-result">
              <el-alert
                :title="workflowResult.message"
                type="success"
                :closable="false"
                show-icon
              />

              <div class="result-actions" v-if="workflowResult.project_id">
                <el-button type="primary" @click="goToProject(workflowResult.project_id)">
                  {{ t('aiChat.viewProject') }}
                </el-button>
                <el-button @click="goToExecutions(workflowResult.project_id)">
                  {{ t('aiChat.viewExecutions') }}
                </el-button>
              </div>

              <!-- Diagnostic Summary -->
              <div v-if="workflowResult.diagnostic_summary" class="diagnostic-card">
                <h4>
                  <el-icon><Cpu /></el-icon> {{ t('aiChat.aiDiagnosis') }}
                </h4>
                <div class="diagnostic-content" v-html="renderMarkdown(workflowResult.diagnostic_summary)"></div>
              </div>
            </div>
          </div>
        </div>

        <!-- Start Button -->
        <div class="workflow-actions" v-if="!workflowRunning && !workflowResult">
          <el-button
            type="primary"
            size="large"
            @click="startWorkflow"
            :loading="workflowLoading"
            :disabled="!workflowApiDoc.trim() || !workflowRequirements.trim()"
          >
            <el-icon><Cpu /></el-icon> {{ t('workflow.start') }}
          </el-button>
        </div>

        <!-- New Workflow Button -->
        <div class="workflow-actions" v-if="workflowResult">
          <el-button @click="resetWorkflow">
            <el-icon><Refresh /></el-icon> {{ t('workflow.new') }}
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Cpu, Document, Delete, UploadFilled, CircleCheckFilled, CircleCloseFilled, InfoFilled, Loading, Refresh } from '@element-plus/icons-vue'
import type { UploadFile, UploadInstance } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { api } from '@/api'
import { useI18n } from '@/composables/useI18n'

const md = new MarkdownIt()
const router = useRouter()
const { t } = useI18n()

const models = ref<any[]>([])
const selectedModel = ref('')

// Workflow state
const workflowApiDoc = ref('')
const workflowRequirements = ref('')
const workflowRunning = ref(false)
const workflowLoading = ref(false)
const workflowSteps = ref<any[]>([])
const workflowResult = ref<any>(null)

// File upload state
const uploadRef = ref<UploadInstance>()
const attachedFile = ref<{ name: string; size: number } | null>(null)

const renderMarkdown = (content: string) => {
  if (!content) return ''
  return md.render(content)
}

// File upload handlers
const handleFileChange = (file: UploadFile) => {
  const raw = file.raw
  if (!raw) return

  const maxSize = 10 * 1024 * 1024 // 10MB
  if (raw.size > maxSize) {
    ElMessage.warning(t('aiChat.fileSizeError'))
    return
  }

  const reader = new FileReader()
  reader.onload = (e) => {
    const content = e.target?.result as string
    workflowApiDoc.value = content
    attachedFile.value = { name: raw.name, size: raw.size }
    ElMessage.success(t('aiChat.fileLoaded', { name: raw.name }))
  }
  reader.onerror = () => {
    ElMessage.error(t('aiChat.fileReadError'))
  }
  reader.readAsText(raw)
}

const handleFileRemove = () => {
  // handled by clearAttachedFile
}

const clearAttachedFile = () => {
  attachedFile.value = null
  uploadRef.value?.clearFiles()
}

const getFileIcon = (filename: string) => {
  const ext = filename.split('.').pop()?.toLowerCase()
  if (ext === 'json') return 'json-icon'
  if (ext === 'yaml' || ext === 'yml') return 'yaml-icon'
  return ''
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

const resetWorkflow = () => {
  workflowApiDoc.value = ''
  workflowRequirements.value = ''
  workflowSteps.value = []
  workflowResult.value = null
  workflowRunning.value = false
  attachedFile.value = null
  uploadRef.value?.clearFiles()
}

const goToProject = (projectId: string) => {
  router.push(`/test-plans?projectId=${projectId}`)
}

const goToExecutions = (projectId: string) => {
  router.push(`/executions?projectId=${projectId}`)
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
.ai-workflow-page {
  min-height: calc(100vh - var(--layout-offset, 132px));
  display: flex;
  flex-direction: column;
}

.page-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 16px;
  background: var(--tavily-bg-card);
  border: 1px solid var(--tavily-border);
  border-radius: var(--tavily-radius-xl);
  padding: 8px 12px;

  .page-title {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    font-size: 14px;
    font-weight: 600;
    color: var(--tavily-text-primary);
  }

  .header-right {
    margin-left: auto;

    .model-select {
      width: 180px;
    }
  }

  @media (max-width: 768px) {
    .header-right {
      width: 100%;
      margin-left: 0;
      margin-top: 4px;

      .model-select {
        width: 100%;
      }
    }
  }
}

// Workflow Styles
.workflow-container {
  flex: 1;
  overflow-y: auto;

  .workflow-sections {
    max-width: 800px;
    margin: 0 auto;
  }

  .workflow-section {
    background: var(--tavily-bg-card);
    border: 1px solid var(--tavily-border);
    border-radius: var(--tavily-radius-xl);
    margin-bottom: 16px;
    overflow: hidden;

    .section-header {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 16px 20px;
      border-bottom: 1px solid var(--tavily-border);
      background: var(--tavily-bg);

      .section-number {
        width: 28px;
        height: 28px;
        border-radius: 50%;
        background: var(--tavily-primary);
        color: #fff;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 13px;
        font-weight: 600;
        flex-shrink: 0;
      }

      .section-title {
        font-size: 15px;
        font-weight: 500;
        color: var(--tavily-text-primary);
      }
    }

    .section-body {
      padding: 20px;
    }

    .section-hint {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-top: 10px;
      font-size: 12px;
      color: var(--tavily-text-muted);
      overflow-wrap: break-word;
      word-break: normal;
    }

    // Upload area styles
    .upload-area {
      margin-bottom: 14px;

      :deep(.el-upload) {
        width: 100%;
      }

      :deep(.el-upload-dragger) {
        width: 100%;
        padding: 20px;
        border: 2px dashed var(--tavily-border);
        border-radius: var(--tavily-radius-md);
        background: var(--tavily-bg);
        transition: all 0.2s ease;

        &:hover {
          border-color: var(--tavily-primary);
          background: var(--tavily-primary-bg);
        }
      }

      .upload-trigger {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 8px;
      }

      .upload-icon {
        font-size: 32px;
        color: var(--tavily-text-muted);
      }

      .upload-text {
        font-size: 13px;

        .upload-link {
          color: var(--tavily-primary);
          cursor: pointer;
          font-weight: 500;
        }

        .upload-or {
          color: var(--tavily-text-muted);
          margin-left: 4px;
        }
      }

      .upload-hint {
        font-size: 11px;
        color: var(--tavily-text-muted);
        text-align: center;
        overflow-wrap: break-word;
        word-break: normal;
        max-width: 100%;
      }

      .attached-file {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 10px;
        padding: 10px 14px;
        background: var(--tavily-success-bg, #f0fdf4);
        border: 1px solid var(--tavily-success-border, rgba(22, 163, 74, 0.2));
        border-radius: var(--tavily-radius-md);
        font-size: 13px;

        .file-type-icon {
          font-size: 20px;
          color: #16A34A;
          flex-shrink: 0;
        }

        .file-name {
          flex: 1;
          font-weight: 500;
          color: var(--tavily-text-primary);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .file-size {
          color: var(--tavily-text-muted);
          font-size: 12px;
          flex-shrink: 0;
        }
      }
    }
  }

  .progress-steps {
    .progress-step {
      display: flex;
      gap: 12px;
      padding: 10px 0;

      .step-indicator {
        flex-shrink: 0;
        width: 24px;
        display: flex;
        align-items: center;
        justify-content: center;

        .el-icon {
          font-size: 20px;

          &.is-loading {
            animation: rotating 2s linear infinite;
            color: var(--tavily-primary);
          }
        }

        .step-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: var(--tavily-border);
        }
      }

      .step-content {
        .step-message {
          font-size: 14px;
          color: var(--tavily-text-secondary);
          line-height: 24px;
        }
      }

      &.done {
        .step-indicator .el-icon { color: #16A34A; }
        .step-content .step-message { color: var(--tavily-text-muted); }
      }

      &.active {
        .step-content .step-message {
          color: var(--tavily-text-primary);
          font-weight: 500;
        }
      }

      &.error {
        .step-indicator .el-icon { color: #DC2626; }
        .step-content .step-message { color: #DC2626; }
      }
    }
  }

  .workflow-result {
    margin-top: 16px;

    .result-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;

      @media (max-width: 768px) {
        .el-button {
          flex: 1;
          min-width: 140px;
          width: auto;
        }
      }
    }

    .diagnostic-card {
      margin-top: 20px;
      padding: 18px;
      background: #fffbeb;
      border: 1px solid rgba(245, 158, 11, 0.2);
      border-radius: var(--tavily-radius-xl);

      h4 {
        font-size: 15px;
        font-weight: 700;
        color: var(--tavily-text-primary);
        margin: 0 0 12px 0;
        display: flex;
        align-items: center;
        gap: 8px;

        .el-icon { color: var(--tavily-primary); }
      }

      .diagnostic-content {
        font-size: 14px;
        line-height: 1.8;
        color: var(--tavily-text-secondary);
        overflow-wrap: break-word;
        word-break: normal;

        :deep(ul) { margin: 0; padding-left: 20px; }
        :deep(li) { margin-bottom: 6px; }
      }
    }
  }

  .workflow-actions {
    text-align: center;
    padding: 20px 0;
  }
}

@keyframes rotating {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
