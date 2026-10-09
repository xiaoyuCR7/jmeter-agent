<template>
  <div class="assertion-patterns-editor">
    <el-form-item :label="t('editor.assertionPatterns.title')">
      <div
        v-for="(pattern, index) in patterns"
        :key="index"
        class="pattern-row"
      >
        <FunctionInput v-model="patterns[index]" size="small" style="flex: 1" />
        <el-button type="danger" size="small" text @click="removePattern(index)">
          <el-icon><Delete /></el-icon>
        </el-button>
      </div>
      <el-button type="primary" text style="margin-top: 8px" @click="addPattern">
        <el-icon><Plus /></el-icon> {{ t('editor.assertionPatterns.add') }}
      </el-button>
    </el-form-item>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Delete, Plus } from '@element-plus/icons-vue'
import { useI18n } from '@/composables/useI18n'
import FunctionInput from './FunctionInput.vue'

const { t } = useI18n()

const props = defineProps<{
  modelValue: Record<string, any>
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, any>]
}>()

const patterns = computed({
  get() {
    const list = props.modelValue['Assertion.test_strings']
    if (!Array.isArray(list)) return []
    return list.map((item: any) => (typeof item === 'string' ? item : ''))
  },
  set(val: string[]) {
    emit('update:modelValue', {
      ...props.modelValue,
      'Assertion.test_strings': val,
    })
  },
})

function addPattern() {
  const list = [...patterns.value, '']
  patterns.value = list
}

function removePattern(index: number) {
  const list = [...patterns.value]
  list.splice(index, 1)
  patterns.value = list
}
</script>

<style scoped lang="scss">
.assertion-patterns-editor {
  .pattern-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }
}
</style>
