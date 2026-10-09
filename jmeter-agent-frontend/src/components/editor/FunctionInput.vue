<template>
  <div class="function-input">
    <el-input v-model="localValue" v-bind="$attrs" />
    <el-button class="fx-btn" size="small" @click="openHelper">
      fx
    </el-button>

    <FunctionHelper
      v-model:visible="helperVisible"
      @insert="handleInsert"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import FunctionHelper from './FunctionHelper.vue'

const props = defineProps<{
  modelValue: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const localValue = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const helperVisible = ref(false)

function openHelper() {
  helperVisible.value = true
}

function handleInsert(funcString: string) {
  const current = props.modelValue || ''
  emit('update:modelValue', current + funcString)
}
</script>

<style scoped lang="scss">
.function-input {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.fx-btn {
  font-family: monospace;
  font-weight: bold;
  padding: 0 8px;
  min-height: 28px;
}
</style>
