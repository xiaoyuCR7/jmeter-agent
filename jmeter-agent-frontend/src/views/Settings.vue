<template>
  <div class="settings">
    <el-row :gutter="16">
      <el-col :span="12" :xs="24" :sm="24" :md="12">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>{{ t('settings.systemInfo') }}</span>
              <div v-if="editingPaths" class="header-actions">
                <el-button type="primary" size="small" @click="savePaths" :loading="savingPaths">
                  {{ t('settings.save') }}
                </el-button>
                <el-button size="small" @click="cancelEdit">{{ t('settings.cancel') }}</el-button>
              </div>
              <el-button v-else type="primary" size="small" @click="startEdit">
                {{ t('settings.editPaths') }}
              </el-button>
            </div>
          </template>
          <div v-if="systemInfo" class="info-list">
            <div class="info-item">
              <span class="label">{{ t('settings.info.platform') }}</span>
              <span class="value">{{ systemInfo.platform }}</span>
            </div>
            <div class="info-item">
              <span class="label">{{ t('settings.info.pythonVersion') }}</span>
              <span class="value">{{ systemInfo.python_version }}</span>
            </div>
            <div class="info-item">
              <span class="label">{{ t('settings.info.jmeterHome') }}</span>
              <span class="value">{{ systemInfo.jmeter_home }}</span>
            </div>
            <div class="info-item">
              <span class="label">{{ t('settings.info.jmeterExecutable') }}</span>
              <span class="value">{{ systemInfo.jmeter_executable || t('settings.info.notFound') }}</span>
            </div>
            <div class="info-item">
              <span class="label">{{ t('settings.info.testPlansDir') }}</span>
              <div v-if="editingPaths" class="value-edit">
                <el-input 
                  v-model="pathsForm.test_plans_dir" 
                  class="path-input"
                  :placeholder="t('settings.info.testPlansDirPlaceholder')"
                />
              </div>
              <span v-else class="value">{{ systemInfo.test_plans_dir }}</span>
            </div>
            <div class="info-item">
              <span class="label">{{ t('settings.info.reportsDir') }}</span>
              <div v-if="editingPaths" class="value-edit">
                <el-input 
                  v-model="pathsForm.reports_dir" 
                  class="path-input"
                  :placeholder="t('settings.info.reportsDirPlaceholder')"
                />
              </div>
              <span v-else class="value">{{ systemInfo.reports_dir }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
      
      <el-col :span="12" :xs="24" :sm="24" :md="12">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>{{ t('settings.aiModelConfig') }}</span>
              <el-button type="primary" size="small" @click="openAddDialog">
                <el-icon><Plus /></el-icon>{{ t('settings.addModel') }}
              </el-button>
            </div>
          </template>
          
          <div class="table-scroll">
            <el-table :data="customModelConfigs" style="width: 100%" v-loading="loading">
              <el-table-column prop="id" :label="t('settings.table.id')" min-width="100" />
            <el-table-column prop="enabled" :label="t('settings.table.status')" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
                  {{ row.enabled ? t('settings.table.enabled') : t('settings.table.disabled') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('settings.table.actions')" min-width="200">
              <template #default="{ row }">
                <el-button link type="success" size="small" :loading="row.testing" @click="handleTest(row)">
                  {{ t('settings.table.test') }}
                </el-button>
                <el-button link type="primary" size="small" @click="openEditDialog(row)">
                  {{ t('settings.table.edit') }}
                </el-button>
                <el-button 
                  link 
                  type="danger" 
                  size="small" 
                  @click="handleDelete(row)"
                >
                  {{ t('settings.table.delete') }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="12" :xs="24" :sm="24" :md="12">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>{{ t('settings.appearance') }}</span>
            </div>
          </template>
          <div class="theme-setting">
            <span class="theme-label">{{ t('settings.theme') }}</span>
            <el-radio-group v-model="appStore.currentTheme" size="small" @change="onThemeChange">
              <el-radio-button label="light">{{ t('settings.light') }}</el-radio-button>
              <el-radio-button label="dark">{{ t('settings.dark') }}</el-radio-button>
            </el-radio-group>
          </div>
          <p class="hint">{{ t('settings.themeHint') }}</p>

          <el-divider />

          <div class="theme-setting">
            <span class="theme-label">{{ t('settings.language') }}</span>
            <el-radio-group v-model="appStore.currentLang" size="small" @change="onLangChange">
              <el-radio-button label="zh-CN">{{ t('settings.language.chinese') }}</el-radio-button>
              <el-radio-button label="en">English</el-radio-button>
            </el-radio-group>
          </div>
          <p class="hint">{{ t('settings.languageHint') }}</p>
        </el-card>
      </el-col>
    </el-row>
    
    <!-- 添加/编辑模型对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEdit ? t('settings.dialog.editTitle') : t('settings.dialog.addTitle')"
      :width="dialogWidth"
      destroy-on-close
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="140px"
        label-position="right"
      >
        <el-form-item :label="t('settings.form.modelId')" prop="id">
          <el-input 
            v-model="form.id" 
            :placeholder="t('settings.form.modelIdPlaceholder')"
            :disabled="isEdit"
          />
          <div class="form-tip">{{ t('settings.form.modelIdTip') }}</div>
        </el-form-item>
        
        <el-form-item :label="t('settings.form.model')" prop="model">
          <el-input v-model="form.model" :placeholder="t('settings.form.modelPlaceholder')" />
          <div class="form-tip">{{ t('settings.form.modelTip') }}</div>
        </el-form-item>
        
        <el-form-item :label="t('settings.form.apiKey')" prop="api_key">
          <el-input 
            v-model="form.api_key" 
            type="password" 
            show-password
            :placeholder="t('settings.form.apiKeyPlaceholder')"
          />
        </el-form-item>
        
        <el-form-item :label="t('settings.form.apiBase')" prop="api_base">
          <el-input v-model="form.api_base" placeholder="https://api.xxx.com/v1" />
          <div class="form-tip">{{ t('settings.form.apiBaseTip') }}</div>
        </el-form-item>
        
        <el-form-item :label="t('settings.form.enabled')" prop="enabled">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('settings.cancel') }}</el-button>
        <el-button type="primary" @click="handleSubmit" :loading="submitting">
          {{ isEdit ? t('settings.save') : t('settings.add') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, reactive, computed, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { api } from '@/api'
import { useAppStore } from '@/store'
import { useI18n } from '@/composables/useI18n'

const { t, setLang } = useI18n()

const dialogWidth = ref('560px')

const handleResize = () => {
  if (window.innerWidth < 640) {
    dialogWidth.value = '95%'
  } else if (window.innerWidth < 768) {
    dialogWidth.value = '500px'
  } else {
    dialogWidth.value = '560px'
  }
}

const systemInfo = ref<any>(null)
const modelConfigs = ref<any[]>([])
const loading = ref(false)

const customModelConfigs = computed(() => {
  // Backend no longer returns built-in models; all returned configs are user-added.
  return modelConfigs.value
})
const dialogVisible = ref(false)
const isEdit = ref(false)
const submitting = ref(false)
const testing = ref(false)
const formRef = ref<any>(null)
const appStore = useAppStore()

const onThemeChange = (value: string) => {
  appStore.setTheme(value as 'light' | 'dark')
}

const onLangChange = (value: string) => {
  setLang(value as 'zh-CN' | 'en')
}

// 路径编辑相关
const editingPaths = ref(false)
const savingPaths = ref(false)
const pathsForm = reactive({
  test_plans_dir: '',
  reports_dir: ''
})

const form = ref({
  id: '',
  model: '',
  api_key: '',
  api_base: '',
  enabled: true
})

const rules = {
  id: [
    { required: true, message: t('settings.rule.modelIdRequired'), trigger: 'blur' },
    { pattern: /^[a-z0-9_-]+$/, message: t('settings.rule.modelIdPattern'), trigger: 'blur' }
  ],
  model: [{ required: true, message: t('settings.rule.modelIdRequired2'), trigger: 'blur' }],
  api_key: [{ required: true, message: t('settings.rule.apiKeyRequired'), trigger: 'blur' }]
}

const fetchData = async () => {
  loading.value = true
  try {
    const [sysRes, configsRes] = await Promise.all([
      api.getSystemInfo(),
      api.getModelConfigs()
    ])
    systemInfo.value = sysRes.data
    modelConfigs.value = configsRes.data
  } catch (error) {
    ElMessage.error(t('settings.message.loadFailed'))
  } finally {
    loading.value = false
  }
}

const startEdit = () => {
  if (systemInfo.value) {
    pathsForm.test_plans_dir = systemInfo.value.test_plans_dir
    pathsForm.reports_dir = systemInfo.value.reports_dir
    editingPaths.value = true
  }
}

const cancelEdit = () => {
  editingPaths.value = false
  pathsForm.test_plans_dir = ''
  pathsForm.reports_dir = ''
}

const savePaths = async () => {
  if (!pathsForm.test_plans_dir || !pathsForm.reports_dir) {
    ElMessage.error(t('settings.message.fillPaths'))
    return
  }
  
  savingPaths.value = true
  try {
    await api.updatePathsConfig({
      test_plans_dir: pathsForm.test_plans_dir,
      reports_dir: pathsForm.reports_dir
    })
    ElMessage.success(t('settings.message.pathsUpdated'))
    editingPaths.value = false
    pathsForm.test_plans_dir = ''
    pathsForm.reports_dir = ''
    await fetchData()
  } catch (error: any) {
    const msg = error.response?.data?.detail || t('settings.message.saveFailed')
    ElMessage.error(msg)
  } finally {
    savingPaths.value = false
  }
}

const openAddDialog = () => {
  isEdit.value = false
  form.value = {
    id: '',
    model: '',
    api_key: '',
    api_base: '',
    enabled: true
  }
  dialogVisible.value = true
}

const openEditDialog = (row: any) => {
  isEdit.value = true
  form.value = {
    id: row.id,
    model: row.model || '',
    api_key: '',
    api_base: row.api_base || '',
    enabled: row.enabled
  }
  dialogVisible.value = true
}

const handleSubmit = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  
  submitting.value = true
  try {
    let apiKey = form.value.api_key
    if (isEdit.value && !apiKey) {
      const original = modelConfigs.value.find((c: any) => c.id === form.value.id)
      if (original) {
        apiKey = original.api_key
      }
    }

    const payload = {
      id: form.value.id,
      model: form.value.model,
      api_key: apiKey,
      api_base: form.value.api_base || null,
      enabled: form.value.enabled
    }
    
    if (isEdit.value) {
      await api.updateModelConfig(form.value.id, payload)
      ElMessage.success(t('settings.message.updateSuccess'))
    } else {
      await api.addModelConfig(payload)
      ElMessage.success(t('settings.message.addSuccess'))
    }
    
    dialogVisible.value = false
    await fetchData()
  } catch (error: any) {
    const msg = error.response?.data?.detail || (isEdit.value ? t('settings.message.updateFailed') : t('settings.message.addFailed'))
    ElMessage.error(msg)
  } finally {
    submitting.value = false
  }
}

const handleTest = async (row: any) => {
  if (testing.value) return
  testing.value = true
  row.testing = true
  try {
    const res = await api.testModelConfig(row.id)
    if (res.data.success) {
      ElMessage.success(res.data.message || t('settings.message.testSuccess'))
    } else {
      ElMessage.error(res.data.message || t('settings.message.testFailed'))
    }
  } catch (error: any) {
    const msg = error.response?.data?.detail || t('settings.message.testFailed')
    ElMessage.error(msg)
  } finally {
    testing.value = false
    row.testing = false
  }
}

const handleDelete = async (row: any) => {
  try {
    await ElMessageBox.confirm(
      t('settings.confirmDeleteModel', { name: row.name }),
      t('settings.confirmDelete'),
      { type: 'warning', confirmButtonText: t('settings.confirmDelete'), cancelButtonText: t('settings.cancel') }
    )
    await api.deleteModelConfig(row.id)
    ElMessage.success(t('settings.message.deleteSuccess'))
    await fetchData()
  } catch (error: any) {
    if (error !== 'cancel') {
      const msg = error.response?.data?.detail || t('settings.message.deleteFailed')
      ElMessage.error(msg)
    }
  }
}

onMounted(() => {
  fetchData()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})
</script>

<style scoped lang="scss">
.settings {
  padding: 0 16px;

  :deep(.el-card) {
    border-radius: var(--tavily-radius-xl);
  }

  @media (min-width: 769px) {
    padding: 0;
  }

  :deep(.el-dialog__body) {
    padding: 24px;

    @media (max-width: 768px) {
      padding: 16px;
    }
  }
  
  :deep(.el-form-item__label) {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex-shrink: 0;
  }
  
  :deep(.el-form-item) {
    margin-bottom: 16px;
    display: flex;
    align-items: flex-start;
  }
  
  :deep(.el-form-item__content) {
    flex: 1;
    min-width: 0;
  }
  
  :deep(.el-input),
  :deep(.el-select) {
    width: 100%;
  }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-weight: 500;
    color: var(--tavily-text-primary);
    flex-wrap: wrap;
    gap: 12px;

    .el-button { width: auto; }

    .header-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
  }

  .info-list {
    .info-item {
      display: flex;
      justify-content: space-between;
      padding: 12px 0;
      border-bottom: 1px solid var(--tavily-border);
      align-items: flex-start;
      flex-wrap: wrap;
      gap: 8px;

      @media (max-width: 768px) {
        flex-direction: column;
        align-items: flex-start;

        .label {
          width: auto;
        }
      }

      &:last-child {
        border-bottom: none;
      }

      .label {
        color: var(--tavily-text-secondary);
        font-size: 14px;
        flex-shrink: 0;
        width: 100%;

        @media (min-width: 769px) {
          width: auto;
        }
      }

      .value {
        font-weight: 500;
        color: var(--tavily-text-primary);
        max-width: 100%;
        text-align: left;
        overflow-wrap: break-word;
        word-break: normal;
        font-size: 14px;

        @media (min-width: 769px) {
          max-width: 60%;
          text-align: right;
        }
      }

      .value-edit {
        flex: 1;
        max-width: 100%;
        margin-left: 0;

        @media (min-width: 769px) {
          max-width: 60%;
          margin-left: auto;
        }

        .path-input {
          width: 100%;
        }
      }
    }
  }

  .hint {
    color: var(--tavily-text-secondary);
    font-size: 13px;
    line-height: 1.5;
  }

  .form-tip {
    font-size: 12px;
    color: var(--tavily-text-muted);
    line-height: 1.4;
    margin-top: 4px;
  }

  .theme-setting {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
    flex-wrap: wrap;
    gap: 12px;

    .theme-label {
      color: var(--tavily-text-secondary);
      font-size: 14px;
    }
  }

  :deep(.el-table) {
    font-size: 13px;

    @media (max-width: 768px) {
      font-size: 12px;
    }
  }

  :deep(.el-table__header-wrapper),
  :deep(.el-table__body-wrapper) {
    overflow-x: auto;
  }

  .table-scroll {
    overflow-x: auto;
  }
}
</style>
