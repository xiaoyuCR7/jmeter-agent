<template>
  <div class="pre-processor-editor">
    <div class="editor-header">
      <span class="editor-title">{{ t('editor.preProcessor.title') }}</span>
      <span class="editor-desc">{{ t('editor.preProcessor.desc') }}</span>
    </div>

    <el-form label-position="top">
      <el-form-item :label="t('editor.scriptLanguage')">
        <el-select v-model="localScript.language" style="width: 200px">
          <el-option label="Groovy" value="groovy" />
          <el-option label="JavaScript" value="javascript" />
          <el-option label="BeanShell" value="beanshell" />
          <el-option label="Java" value="java" />
        </el-select>
      </el-form-item>

      <el-form-item :label="t('editor.scriptContent')">
        <el-input
          v-model="localScript.script"
          type="textarea"
          :rows="12"
          class="script-textarea"
          :placeholder="placeholderText"
        />
      </el-form-item>

      <div class="quick-actions">
        <span class="quick-label">{{ t('editor.quickInsert') }}:</span>
        <el-button size="small" text @click="insertSnippet('setVar')">{{ t('editor.snippet.setVar') }}</el-button>
        <el-button size="small" text @click="insertSnippet('getVar')">{{ t('editor.snippet.getVar') }}</el-button>
        <el-button size="small" text @click="insertSnippet('timestamp')">{{ t('editor.snippet.timestamp') }}</el-button>
        <el-button size="small" text @click="insertSnippet('random')">{{ t('editor.snippet.random') }}</el-button>
        <el-button size="small" text type="primary" @click="$emit('openHelper')">
          <span class="fx-label">fx</span> {{ t('editor.functionHelper') }}
        </el-button>
      </div>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '@/composables/useI18n'

const { t } = useI18n()

interface ScriptConfig {
  language: string
  script: string
}

const props = defineProps<{
  modelValue: ScriptConfig
}>()

const emit = defineEmits<{
  'update:modelValue': [value: ScriptConfig]
  'openHelper': []
}>()

const localScript = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const snippets: Record<string, string> = {
  setVar: 'vars.put("variableName", "value")',
  getVar: 'vars.get("variableName")',
  timestamp: 'System.currentTimeMillis()',
  random: 'new Random().nextInt(1000)',
}

const placeholderText = computed(() => t('editor.preProcessor.placeholder'))

function insertSnippet(key: string) {
  const snippet = snippets[key] || ''
  const current = localScript.value.script || ''
  emit('update:modelValue', {
    ...localScript.value,
    script: current ? current + '\n' + snippet : snippet
  })
}
</script>

<style scoped lang="scss">
.pre-processor-editor {
  padding: 4px 0;

  .editor-header {
    margin-bottom: 16px;
    .editor-title {
      font-size: 14px;
      font-weight: 500;
      color: var(--app-text);
    }
    .editor-desc {
      display: block;
      margin-top: 4px;
      font-size: 12px;
      color: var(--app-text-muted);
    }
  }

  .script-textarea {
    :deep(.el-textarea__inner) {
      font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
      font-size: 13px;
      line-height: 1.5;
      background: var(--app-surface);
      border-color: var(--app-border);
      color: var(--app-text);
    }
  }

  .quick-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 4px;

    .quick-label {
      font-size: 12px;
      color: var(--app-text-muted);
      margin-right: 4px;
    }

    .fx-label {
      font-family: monospace;
      font-weight: bold;
    }
  }
}
</style>
