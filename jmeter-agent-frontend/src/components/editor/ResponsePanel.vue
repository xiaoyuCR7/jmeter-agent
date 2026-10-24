<template>
  <div class="response-panel">
    <div class="response-header">
      <div class="response-tabs">
        <span
          v-for="tab in tabs"
          :key="tab.name"
          :class="['response-tab', { active: activeTab === tab.name }]"
          @click="activeTab = tab.name"
        >
          {{ tab.label }}
        </span>
      </div>
      <div v-if="response" class="response-meta">
        <el-tag :type="statusTagType" size="small">{{ response.status }} {{ response.statusText }}</el-tag>
        <span class="meta-item">{{ response.time }} ms</span>
        <span class="meta-item">{{ formatSize(response.size) }}</span>
      </div>
    </div>
    <div class="response-body">
      <template v-if="!response">
        <div class="response-placeholder">
          <el-icon size="40" color="var(--app-text-muted)"><Monitor /></el-icon>
          <p>{{ t('editor.responsePanel.clickSend') }}</p>
        </div>
      </template>
      <template v-else>
        <pre v-if="activeTab === 'body'" class="response-content">{{ response.body }}</pre>
        <pre v-else-if="activeTab === 'headers'" class="response-content">{{ response.headersText }}</pre>
        <div v-else class="response-placeholder">
          <p>{{ activeTab === 'cookies' ? t('editor.responsePanel.noCookie') : t('editor.responsePanel.noConsole') }}</p>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Monitor } from '@element-plus/icons-vue'
import { useI18n } from '@/composables/useI18n'

const { t } = useI18n()

export interface DebugResponse {
  status: number
  statusText: string
  time: number
  size: number
  body: string
  headersText: string
}

defineProps<{
  response: DebugResponse | null
}>()

const activeTab = ref('body')

const tabs = computed(() => [
  { name: 'body', label: 'Body' },
  { name: 'headers', label: 'Headers' },
  { name: 'cookies', label: 'Cookies' },
  { name: 'console', label: t('editor.responsePanel.console') },
])

const statusTagType = computed(() => {
  return 'success'
})

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}
</script>

<style scoped lang="scss">
.response-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--app-surface);
  border-top: 1px solid var(--app-border);

  .response-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 16px;
    border-bottom: 1px solid var(--app-border);
    min-height: 40px;
    flex-shrink: 0;
    flex-wrap: wrap;
    gap: 8px;

    .response-tabs {
      display: flex;
      gap: 4px;

      .response-tab {
        padding: 10px 12px;
        font-size: 13px;
        color: var(--app-text-secondary);
        cursor: pointer;
        border-bottom: 2px solid transparent;
        transition: all 0.15s;

        &:hover { color: var(--app-primary); }
        &.active {
          color: var(--app-primary);
          border-bottom-color: var(--app-primary);
          font-weight: 500;
        }
      }
    }

    .response-meta {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 12px;
      color: var(--app-text-muted);

      .meta-item { font-family: monospace; }
    }
  }

  .response-body {
    flex: 1;
    overflow: auto;
    min-height: 0;
  }

  .response-placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--app-text-muted);
    font-size: 13px;
    gap: 8px;

    p { margin: 0; }
  }

  .response-content {
    margin: 0;
    padding: 16px;
    font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
    font-size: 13px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: normal;
    overflow-wrap: break-word;
    color: var(--app-text);
  }
}
</style>
