<template>
  <span :class="['status', status]">
    <el-icon v-if="status === 'running'" class="is-loading"><Loading /></el-icon>
    <span v-else class="dot"></span>
    {{ t(`executions.status.${status}`) }}
  </span>
</template>

<script setup lang="ts">
import { Loading } from '@element-plus/icons-vue'
import { useI18n } from '@/composables/useI18n'

defineProps<{ status: string }>()
const { t } = useI18n()
</script>

<style scoped lang="scss">
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--app-text-secondary);
  white-space: nowrap;

  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--app-text-muted); }
  &.completed .dot { background: var(--app-success); }
  &.failed { color: var(--app-danger); .dot { background: var(--app-danger); } }
  &.running { color: var(--app-primary); }
  &.pending .dot { background: var(--app-warning); }
}
</style>
