<template>
  <div class="env-selector">
    <div class="env-selector-trigger">
      <el-icon class="env-icon"><Monitor /></el-icon>
      <el-select
        :model-value="activeEnvId"
        @update:model-value="handleChange"
        :placeholder="t('env.selector.placeholder')"
        size="small"
        class="env-select"
        :disabled="!environments.length"
      >
        <template #prefix>
          <el-dropdown v-if="activeEnv" trigger="hover">
            <span class="env-vars-preview-trigger">
              <el-icon><Key /></el-icon>
            </span>
            <template #dropdown>
              <div class="env-vars-preview">
                <div class="preview-header">{{ t('env.selector.variables') }}</div>
                <div v-if="activeEnv.variables && Object.keys(activeEnv.variables).length > 0" class="preview-vars">
                  <div v-for="(value, key) in activeEnv.variables" :key="key" class="preview-var-item">
                    <span class="preview-var-name">{{ key }}</span>
                    <span class="preview-var-value">{{ value }}</span>
                  </div>
                </div>
                <div v-else class="preview-empty">{{ t('env.selector.noVariables') }}</div>
              </div>
            </template>
          </el-dropdown>
        </template>
        <el-option
          v-for="env in environments"
          :key="env.id"
          :label="env.name"
          :value="env.id"
        >
          <div class="env-option">
            <span class="env-option-name">{{ env.name }}</span>
            <span class="env-option-url">{{ env.base_url || t('env.selector.url') }}</span>
            <div class="env-option-actions">
              <el-button text size="small" @click.stop="handleCopyEnv(env)" :title="t('env.selector.copy')">
                <el-icon><CopyDocument /></el-icon>
              </el-button>
            </div>
          </div>
        </el-option>
      </el-select>
      <el-button
        text
        size="small"
        class="env-manage-btn"
        @click="$emit('manage')"
        :title="t('env.selector.manage')"
      >
        <el-icon><Setting /></el-icon>
      </el-button>
    </div>
    <div v-if="activeEnv" class="env-info">
      <el-tag size="small" type="success">{{ activeEnv.name }}</el-tag>
      <span class="env-url" v-if="activeEnv.base_url">{{ activeEnv.base_url }}</span>
      <span class="env-var-count" v-if="varCount > 0">{{ varCount }}{{ t('env.selector.variables') }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Monitor, Setting, Key, CopyDocument } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useI18n } from '@/composables/useI18n'
import type { EnvironmentInfo } from '@/store'

const { t } = useI18n()

const props = defineProps<{
  environments: EnvironmentInfo[]
  activeEnvId: string | null
  activeEnv: EnvironmentInfo | null
}>()

const emit = defineEmits<{
  'update:activeEnvId': [value: string]
  'manage': []
  'copyEnv': [env: EnvironmentInfo]
}>()

const varCount = computed(() => {
  if (!props.activeEnv?.variables) return 0
  return Object.keys(props.activeEnv.variables).length
})

function handleChange(val: string) {
  emit('update:activeEnvId', val)
}

function handleCopyEnv(env: EnvironmentInfo) {
  emit('copyEnv', env)
  ElMessage.success(t('env.selector.copySuccess', { name: env.name }))
}
</script>

<style scoped lang="scss">
.env-selector {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;

  .env-selector-trigger {
    display: flex;
    align-items: center;
    gap: 4px;
    background: var(--tavily-bg-page);
    border: 1px solid var(--tavily-border);
    border-radius: var(--tavily-radius-md);
    padding: 4px 4px 4px 10px;

    .env-icon {
      color: var(--tavily-text-secondary);
      font-size: 15px;
    }

    .env-select {
      width: 150px;
      :deep(.el-input__wrapper) {
        box-shadow: none;
        background: transparent;
        padding: 0;
      }
    }

    .env-vars-preview-trigger {
      cursor: pointer;
      color: var(--tavily-text-secondary);
      margin-right: 4px;
      &:hover { color: var(--tavily-primary); }
    }

    .env-manage-btn {
      color: var(--tavily-text-secondary);
      &:hover { color: var(--tavily-primary); }
    }
  }

  .env-info {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;

    .env-url {
      color: var(--tavily-text-secondary);
      max-width: 200px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .env-var-count {
      color: var(--tavily-text-muted);
    }
  }
}

.env-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;

  .env-option-main {
    flex: 1;
  }

  .env-option-name {
    font-weight: 500;
    display: block;
  }

  .env-option-url {
    font-size: 12px;
    color: var(--tavily-text-secondary);
    display: block;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .env-option-actions {
    .el-button {
      color: var(--tavily-text-muted);
      &:hover { color: var(--tavily-primary); }
    }
  }
}

.env-vars-preview {
  min-width: 300px;
  padding: 8px 0;

  .preview-header {
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 500;
    border-bottom: 1px solid var(--tavily-border);
    margin-bottom: 4px;
  }

  .preview-vars {
    max-height: 200px;
    overflow-y: auto;
  }

  .preview-var-item {
    display: flex;
    justify-content: space-between;
    padding: 6px 12px;
    font-size: 12px;
    &:hover { background: var(--tavily-primary-bg); }
  }

  .preview-var-name {
    font-weight: 500;
    color: var(--tavily-text-primary);
    min-width: 100px;
  }

  .preview-var-value {
    color: var(--tavily-text-secondary);
    flex: 1;
    text-align: right;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding-left: 12px;
  }

  .preview-empty {
    padding: 16px 12px;
    text-align: center;
    color: var(--tavily-text-muted);
    font-size: 12px;
  }
}
</style>
