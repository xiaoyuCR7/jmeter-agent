<template>
  <div class="post-processor-editor">
    <div class="editor-header">
      <span class="editor-title">{{ t('editor.postProcessor.title') }}</span>
      <span class="editor-desc">{{ t('editor.postProcessor.desc') }}</span>
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

      <div class="extractors-section">
        <div class="section-label">{{ t('editor.extractor.title') }}</div>
        <el-collapse v-model="activeExtractors">
          <el-collapse-item :title="t('editor.extractor.jsonPath')" name="json">
            <el-form label-position="top" size="small">
              <el-row :gutter="12">
                <el-col :span="12">
                  <el-form-item :label="t('editor.extractor.varName')">
                    <el-input v-model="extractors.json.refName" placeholder="myVar" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item :label="t('editor.extractor.jsonPathExpr')">
                    <el-input v-model="extractors.json.jsonPath" placeholder="$.data.id" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-row :gutter="12">
                <el-col :span="8">
                  <el-form-item :label="t('editor.extractor.matchNo')">
                    <el-input-number v-model="extractors.json.matchNo" :min="0" style="width: 100%" />
                  </el-form-item>
                </el-col>
                <el-col :span="16">
                  <el-form-item :label="t('editor.extractor.defaultValue')">
                    <el-input v-model="extractors.json.defaultValue" placeholder="NOT_FOUND" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-button size="small" type="primary" @click="addJsonExtractor">{{ t('editor.extractor.addJson') }}</el-button>
            </el-form>
          </el-collapse-item>

          <el-collapse-item :title="t('editor.extractor.regex')" name="regex">
            <el-form label-position="top" size="small">
              <el-row :gutter="12">
                <el-col :span="12">
                  <el-form-item :label="t('editor.extractor.varName')">
                    <el-input v-model="extractors.regex.refName" placeholder="myVar" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item :label="t('editor.extractor.regexExpr')">
                    <el-input v-model="extractors.regex.regex" placeholder='&lt;input name="token" value="(.+?)"/&gt;' />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-row :gutter="12">
                <el-col :span="6">
                  <el-form-item :label="t('editor.extractor.template')">
                    <el-input v-model="extractors.regex.template" placeholder="$1$" />
                  </el-form-item>
                </el-col>
                <el-col :span="6">
                  <el-form-item :label="t('editor.extractor.matchNo')">
                    <el-input-number v-model="extractors.regex.matchNo" :min="0" style="width: 100%" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item :label="t('editor.extractor.defaultValue')">
                    <el-input v-model="extractors.regex.defaultValue" placeholder="NOT_FOUND" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-button size="small" type="primary" @click="addRegexExtractor">{{ t('editor.extractor.addRegex') }}</el-button>
            </el-form>
          </el-collapse-item>

          <el-collapse-item :title="t('editor.extractor.boundary')" name="boundary">
            <el-form label-position="top" size="small">
              <el-row :gutter="12">
                <el-col :span="8">
                  <el-form-item :label="t('editor.extractor.varName')">
                    <el-input v-model="extractors.boundary.refName" placeholder="myVar" />
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item :label="t('editor.extractor.leftBoundary')">
                    <el-input v-model="extractors.boundary.left" placeholder="&lt;token&gt;" />
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item :label="t('editor.extractor.rightBoundary')">
                    <el-input v-model="extractors.boundary.right" placeholder="&lt;/token&gt;" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-row :gutter="12">
                <el-col :span="12">
                  <el-form-item :label="t('editor.extractor.matchNo')">
                    <el-input-number v-model="extractors.boundary.matchNo" :min="0" style="width: 100%" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item :label="t('editor.extractor.defaultValue')">
                    <el-input v-model="extractors.boundary.defaultValue" placeholder="NOT_FOUND" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-button size="small" type="primary" @click="addBoundaryExtractor">{{ t('editor.extractor.addBoundary') }}</el-button>
            </el-form>
          </el-collapse-item>

          <el-collapse-item :title="t('editor.extractor.xpath')" name="xpath">
            <el-form label-position="top" size="small">
              <el-row :gutter="12">
                <el-col :span="12">
                  <el-form-item :label="t('editor.extractor.varName')">
                    <el-input v-model="extractors.xpath.refName" placeholder="myVar" />
                  </el-form-item>
                </el-col>
                <el-col :span="12">
                  <el-form-item :label="t('editor.extractor.xpathQuery')">
                    <el-input v-model="extractors.xpath.xpathQuery" placeholder="//input[@name='token']/@value" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-row :gutter="12">
                <el-col :span="8">
                  <el-form-item :label="t('editor.extractor.matchNo')">
                    <el-input-number v-model="extractors.xpath.matchNo" :min="0" style="width: 100%" />
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item :label="t('editor.extractor.namespaces')">
                    <el-input v-model="extractors.xpath.namespaces" placeholder="命名空间" />
                  </el-form-item>
                </el-col>
                <el-col :span="8">
                  <el-form-item :label="t('editor.extractor.defaultValue')">
                    <el-input v-model="extractors.xpath.defaultValue" placeholder="NOT_FOUND" />
                  </el-form-item>
                </el-col>
              </el-row>
              <el-button size="small" type="primary" @click="addXPathExtractor">{{ t('editor.extractor.addXpath') }}</el-button>
            </el-form>
          </el-collapse-item>
        </el-collapse>

        <div v-if="addedExtractors.length > 0" class="added-extractors">
          <el-tag
            v-for="(ext, idx) in addedExtractors"
            :key="idx"
            closable
            size="small"
            type="success"
            @close="removeExtractor(idx)"
          >
            {{ ext.type }}: {{ ext.refName }}
          </el-tag>
        </div>
      </div>

      <div class="quick-actions">
        <span class="quick-label">{{ t('editor.quickInsert') }}:</span>
        <el-button size="small" text @click="insertSnippet('getResponse')">{{ t('editor.snippet.getResponse') }}</el-button>
        <el-button size="small" text @click="insertSnippet('setVar')">{{ t('editor.snippet.setVar') }}</el-button>
        <el-button size="small" text @click="insertSnippet('assert')">{{ t('editor.snippet.assert') }}</el-button>
        <el-button size="small" text @click="insertSnippet('log')">{{ t('editor.snippet.log') }}</el-button>
      </div>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from '@/composables/useI18n'

const { t } = useI18n()

interface ScriptConfig {
  language: string
  script: string
}

interface ExtractorInfo {
  type: string
  refName: string
  config: Record<string, any>
}

const props = defineProps<{
  modelValue: ScriptConfig
}>()

const emit = defineEmits<{
  'update:modelValue': [value: ScriptConfig]
  'addExtractor': [extractor: ExtractorInfo]
}>()

const activeExtractors = ref<string[]>([])
const addedExtractors = ref<ExtractorInfo[]>([])

const extractors = reactive({
  json: { refName: '', jsonPath: '', matchNo: 1, defaultValue: 'NOT_FOUND' },
  regex: { refName: '', regex: '', template: '$1$', matchNo: 1, defaultValue: 'NOT_FOUND' },
  boundary: { refName: '', left: '', right: '', matchNo: 1, defaultValue: 'NOT_FOUND' },
  xpath: { refName: '', xpathQuery: '', matchNo: 1, namespaces: '', defaultValue: 'NOT_FOUND' },
})

const localScript = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const snippets: Record<string, string> = {
  getResponse: 'prev.getResponseDataAsString()',
  setVar: 'vars.put("variableName", "value")',
  assert: 'if (!prev.getResponseCode()?.equals("200")) {\n  AssertionResult.setFailure(true)\n  AssertionResult.setFailureMessage("Expected 200")\n}',
  log: 'log.info("Response: " + prev.getResponseDataAsString())',
}

const placeholderText = computed(() => t('editor.postProcessor.placeholder'))

function insertSnippet(key: string) {
  const snippet = snippets[key] || ''
  const current = localScript.value.script || ''
  emit('update:modelValue', {
    ...localScript.value,
    script: current ? current + '\n' + snippet : snippet
  })
}

function addJsonExtractor() {
  const e = extractors.json
  if (!e.refName || !e.jsonPath) {
    ElMessage.warning(t('editor.message.jsonPathRequired'))
    return
  }
  const ext: ExtractorInfo = { type: 'JSONPath', refName: e.refName, config: { ...e } }
  addedExtractors.value.push(ext)
  emit('addExtractor', ext)
  e.refName = ''; e.jsonPath = ''; e.matchNo = 1; e.defaultValue = 'NOT_FOUND'
  activeExtractors.value = []
}

function addRegexExtractor() {
  const e = extractors.regex
  if (!e.refName || !e.regex) {
    ElMessage.warning(t('editor.message.regexRequired'))
    return
  }
  const ext: ExtractorInfo = { type: 'Regex', refName: e.refName, config: { ...e } }
  addedExtractors.value.push(ext)
  emit('addExtractor', ext)
  e.refName = ''; e.regex = ''; e.template = '$1$'; e.matchNo = 1; e.defaultValue = 'NOT_FOUND'
  activeExtractors.value = []
}

function addBoundaryExtractor() {
  const e = extractors.boundary
  if (!e.refName || (!e.left && !e.right)) {
    ElMessage.warning(t('editor.message.boundaryRequired'))
    return
  }
  const ext: ExtractorInfo = { type: 'Boundary', refName: e.refName, config: { ...e } }
  addedExtractors.value.push(ext)
  emit('addExtractor', ext)
  e.refName = ''; e.left = ''; e.right = ''; e.matchNo = 1; e.defaultValue = 'NOT_FOUND'
  activeExtractors.value = []
}

function addXPathExtractor() {
  const e = extractors.xpath
  if (!e.refName || !e.xpathQuery) {
    ElMessage.warning(t('editor.message.xpathRequired'))
    return
  }
  const ext: ExtractorInfo = { type: 'XPath', refName: e.refName, config: { ...e } }
  addedExtractors.value.push(ext)
  emit('addExtractor', ext)
  e.refName = ''; e.xpathQuery = ''; e.matchNo = 1; e.namespaces = ''; e.defaultValue = 'NOT_FOUND'
  activeExtractors.value = []
}

function removeExtractor(idx: number) {
  addedExtractors.value.splice(idx, 1)
}
</script>

<style scoped lang="scss">
.post-processor-editor {
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

  .extractors-section {
    margin-top: 20px;
    padding-top: 16px;
    border-top: 1px solid var(--app-border);

    .section-label {
      font-size: 13px;
      font-weight: 500;
      color: var(--app-text);
      margin-bottom: 8px;
    }

    :deep(.el-collapse) {
      border: none;
      .el-collapse-item__header {
        font-size: 13px;
        color: var(--app-text-secondary);
        border: none;
        background: transparent;
        padding: 4px 0;
      }
      .el-collapse-item__wrap {
        border: none;
        background: transparent;
      }
      .el-collapse-item__content {
        padding: 8px 0 16px;
      }
    }

    .added-extractors {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }
  }

  .quick-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 8px;

    .quick-label {
      font-size: 12px;
      color: var(--app-text-muted);
      margin-right: 4px;
    }
  }
}
</style>
