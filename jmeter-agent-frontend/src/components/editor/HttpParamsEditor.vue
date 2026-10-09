<template>
  <div class="http-params-editor">
    <el-form-item :label="t('editor.params.title')">
      <div class="table-scroll">
      <el-table :data="params" border size="small" style="width: 100%">
        <el-table-column :label="t('editor.params.name')" min-width="140">
          <template #default="{ $index }">
            <el-input v-model="params[$index]['Argument.name']" size="small" />
          </template>
        </el-table-column>
        <el-table-column :label="t('editor.params.value')" min-width="200">
          <template #default="{ $index }">
            <FunctionInput v-model="params[$index]['Argument.value']" size="small" />
          </template>
        </el-table-column>
        <el-table-column :label="t('editor.params.encode')" width="80">
          <template #default="{ $index }">
            <el-switch v-model="params[$index]['HTTPArgument.always_encode']" size="small" />
          </template>
        </el-table-column>
        <el-table-column width="80">
          <template #default="{ $index }">
            <el-button type="danger" size="small" text @click="removeParam($index)">{{ t('editor.common.delete') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      </div>
      <el-button type="primary" text style="margin-top: 8px" @click="addParam">
        <el-icon><Plus /></el-icon> {{ t('editor.params.add') }}
      </el-button>
    </el-form-item>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '@/composables/useI18n'
import FunctionInput from './FunctionInput.vue'

const { t } = useI18n()

const props = defineProps<{ modelValue: Record<string, any> }>()
const emit = defineEmits<{ 'update:modelValue': [value: Record<string, any>] }>()

const params = computed({
  get() {
    const list = props.modelValue['__http_params']
    if (!Array.isArray(list)) return []
    return list.map((item: any) => ({
      _elementType: 'HTTPArgument',
      _name: item._name || item['Argument.name'] || '',
      'Argument.name': item['Argument.name'] || '',
      'Argument.value': item['Argument.value'] || '',
      'Argument.metadata': item['Argument.metadata'] || '=',
      'HTTPArgument.always_encode': item['HTTPArgument.always_encode'] || false,
      'HTTPArgument.use_equals': item['HTTPArgument.use_equals'] !== false,
    }))
  },
  set(val) {
    emit('update:modelValue', { ...props.modelValue, __http_params: val })
  },
})

function addParam() {
  params.value = [
    ...params.value,
    {
      _elementType: 'HTTPArgument',
      _name: '',
      'Argument.name': '',
      'Argument.value': '',
      'Argument.metadata': '=',
      'HTTPArgument.always_encode': false,
      'HTTPArgument.use_equals': true,
    },
  ]
}

function removeParam(index: number) {
  const list = [...params.value]
  list.splice(index, 1)
  params.value = list
}
</script>
