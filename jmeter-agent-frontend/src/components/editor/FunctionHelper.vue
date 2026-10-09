<template>
  <el-dialog
    :model-value="visible"
    @update:model-value="$emit('update:visible', $event)"
    :title="t('editor.functionHelper.title')"
    width="600px"
    style="max-width: calc(100vw - 32px);"
  >
    <div class="function-helper">
      <el-form label-position="top">
        <el-form-item :label="t('editor.functionHelper.select')">
          <el-select v-model="selectedFunc" filterable style="width: 100%" @change="onFuncChange">
            <el-option
              v-for="f in functions"
              :key="f.name"
              :label="f.label"
              :value="f.name"
            />
          </el-select>
        </el-form-item>

        <template v-if="currentFunc">
          <el-alert :title="currentFunc.description" type="info" :closable="false" style="margin-bottom: 16px" />

          <el-form-item
            v-for="param in currentFunc.params"
            :key="param.name"
            :label="param.label + (param.required ? ' *' : '')"
          >
            <el-select
              v-if="param.type === 'select'"
              v-model="paramValues[param.name]"
              style="width: 100%"
            >
              <el-option
                v-for="opt in param.options"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
            <el-input-number
              v-else-if="param.type === 'number'"
              v-model="paramValues[param.name]"
              style="width: 100%"
            />
            <el-input
              v-else
              v-model="paramValues[param.name]"
              :placeholder="param.placeholder || ''"
            />
          </el-form-item>

          <el-form-item :label="t('editor.functionHelper.preview')">
            <el-input v-model="preview" readonly />
          </el-form-item>

          <el-form-item v-if="currentFunc.example">
            <el-text type="info">{{ t('editor.functionHelper.example') }} {{ currentFunc.example }}</el-text>
          </el-form-item>
        </template>
      </el-form>
    </div>

    <template #footer>
      <el-button @click="$emit('update:visible', false)">{{ t('editor.functionHelper.cancel') }}</el-button>
      <el-button type="primary" @click="insert">{{ t('editor.functionHelper.insert') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { api } from '@/api/index'
import { useI18n } from '@/composables/useI18n'

const { t } = useI18n()

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  insert: [funcString: string]
}>()

const functions = ref<any[]>([])
const selectedFunc = ref('')
const paramValues = ref<Record<string, any>>({})

const currentFunc = computed(() => {
  return functions.value.find((f) => f.name === selectedFunc.value)
})

const preview = computed(() => {
  if (!currentFunc.value) return ''
  const args = currentFunc.value.params
    .map((p: any) => paramValues.value[p.name] !== undefined ? String(paramValues.value[p.name]) : '')
    .join(',')
  return `\${${currentFunc.value.name}(${args})}`
})

function onFuncChange() {
  paramValues.value = {}
  const f = currentFunc.value
  if (f && f.params) {
    for (const p of f.params) {
      paramValues.value[p.name] = p.type === 'number' ? 0 : ''
    }
  }
}

function insert() {
  emit('insert', preview.value)
  emit('update:visible', false)
}

watch(
  () => props.visible,
  (val) => {
    if (val && functions.value.length === 0) {
      api.getFunctions().then((res: any) => {
        functions.value = res.data || []
      })
    }
  }
)
</script>

<style scoped lang="scss">
.function-helper {
  max-height: 500px;
  overflow-y: auto;
}
</style>
