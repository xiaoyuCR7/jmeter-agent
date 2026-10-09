<template>
  <div class="import-page">
    <div class="page-desc">{{ t('openapi.subtitle') }}</div>

    <el-card class="import-card">
      <template #header><span>{{ t('openapi.title') }}</span></template>
      <el-form @submit.prevent="doImport">
        <el-form-item :label="t('openapi.docUrl')">
          <el-input v-model="url" placeholder="https://api.example.com/openapi.json" size="large">
            <template #append><el-button class="import-btn" type="primary" @click="doImport" :loading="loading">{{ t('openapi.import') }}</el-button></template>
          </el-input>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card v-if="result" class="result-card">
      <template #header><span>{{ t('openapi.result') }}</span></template>
      <div v-if="result.success">
        <el-descriptions :column="isMobile ? 1 : 2" border size="small">
          <el-descriptions-item :label="t('openapi.projectName')">{{ result.project_name }}</el-descriptions-item>
          <el-descriptions-item :label="t('openapi.projectId')">{{ result.project_id }}</el-descriptions-item>
          <el-descriptions-item :label="t('openapi.apiCount')">{{ result.endpoints_found }}</el-descriptions-item>
          <el-descriptions-item :label="t('openapi.authMode')">{{ result.auth_detected || t('openapi.none') }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="result.jmx_paths?.length" class="jmx-list">
          <h4>{{ t('openapi.generatedPlans') }}</h4>
          <div class="jmx-tags">
            <el-tag v-for="p in result.jmx_paths" :key="p" type="success">{{ p }}</el-tag>
          </div>
        </div>
        <div v-if="result.warnings?.length" class="warnings">
          <el-alert v-for="w in result.warnings" :key="w" :title="w" type="warning" show-icon :closable="false" style="margin:4px 0"/>
        </div>
        <el-button type="primary" @click="$router.push('/test-plans')" style="margin-top:12px">{{ t('openapi.viewPlan') }}</el-button>
      </div>
      <el-alert v-else :title="result.error" type="error" show-icon :closable="false"/>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '@/api'
import { useI18n } from '@/composables/useI18n'
import { ElMessage } from 'element-plus'

const { t } = useI18n()

const isMobile = ref(false)
const updateIsMobile = () => {
  isMobile.value = window.innerWidth <= 768
}

onMounted(() => {
  updateIsMobile()
  window.addEventListener('resize', updateIsMobile)
})

onUnmounted(() => {
  window.removeEventListener('resize', updateIsMobile)
})

const url = ref('')
const loading = ref(false)
const result = ref<any>(null)

async function doImport() {
  if (!url.value) return ElMessage.warning(t('openapi.inputRequired'))
  loading.value = true
  result.value = null
  try {
    const res = await api.importOpenAPI(url.value)
    result.value = res.data
    if (res.data.success) {
      ElMessage.success(t('openapi.success', { count: res.data.endpoints_found, name: res.data.project_name }))
    } else {
      ElMessage.error(res.data.error)
    }
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('openapi.failed'))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped lang="scss">
.import-page {
  max-width: 800px;
  margin: 0 auto;

  @media (max-width: 768px) {
    padding: 0 16px;
    box-sizing: border-box;
  }

  .page-desc { color: var(--tavily-text-muted); margin-bottom: 16px; font-size: 14px; }
  .import-card { margin-bottom: 20px; border-radius: var(--tavily-radius-xl); }
  .result-card { border-radius: var(--tavily-radius-xl); }
  .jmx-list { margin-top: 12px; h4 { font-size: 14px; margin: 0 0 6px; } }
  .jmx-tags { display: flex; flex-wrap: wrap; gap: 8px;
    .el-tag { word-break: break-all; white-space: normal; height: auto; line-height: 1.4; }
  }
  .warnings { margin-top: 8px; }

  .import-btn {
    width: auto;
  }
}
</style>
