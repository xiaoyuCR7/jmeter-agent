<template>
  <div class="function-textarea">
    <el-input v-model="localValue" type="textarea" :rows="rows" v-bind="$attrs" />
    <el-button class="fx-btn" size="small" @click="helperVisible = true">fx</el-button>
    <FunctionHelper v-model:visible="helperVisible" @insert="handleInsert" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import FunctionHelper from './FunctionHelper.vue'

const props = withDefaults(defineProps<{
  modelValue: string
  rows?: number
}>(), {
  rows: 6,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const localValue = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const helperVisible = ref(false)

function handleInsert(funcString: string) {
  emit('update:modelValue', (props.modelValue || '') + funcString)
}
</script>

<style scoped lang="scss">
.function-textarea {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;

  :deep(.el-textarea) {
    flex: 1;
  }
}

.fx-btn {
  font-family: monospace;
  font-weight: bold;
  padding: 0 8px;
  min-height: 28px;
  margin-top: 4px;
}
</style>
