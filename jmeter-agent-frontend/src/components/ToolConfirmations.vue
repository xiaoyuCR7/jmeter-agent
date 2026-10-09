<template>
  <el-dialog :model-value="pending.length > 0" title="确认 Agent 操作 / Confirm agent action"
             :close-on-click-modal="false" :close-on-press-escape="false" :show-close="false" width="min(680px, 95vw)">
    <template v-if="pending[0]">
      <p>Agent 正在等待确认。请核对工具和参数；拒绝或超时将停止这项操作。</p>
      <p><strong>{{ pending[0].tool }}</strong></p>
      <pre>{{ JSON.stringify(pending[0].arguments, null, 2) }}</pre>
    </template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template #footer>
      <el-button :disabled="busy" @click="decide(false)">拒绝 / Deny</el-button>
      <el-button type="primary" :loading="busy" @click="decide(true)">确认执行一次 / Allow once</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '@/api'
const pending = ref<any[]>([])
const busy = ref(false)
const error = ref('')
let stopped = false
let timer: ReturnType<typeof setTimeout> | undefined
async function poll() {
  try {
    const result = await api.pendingConfirmations()
    if (!stopped) pending.value = result.data
  } catch { /* Authentication errors are handled by the shared transport. */ }
  finally { if (!stopped) timer = setTimeout(poll, 5000) }
}
async function decide(approved: boolean) {
  const item = pending.value[0]
  if (!item || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await api.decideConfirmation(item.id, approved)
    pending.value = pending.value.filter(request => request.id !== item.id)
  } catch {
    error.value = '确认未生效，可能已过期。请查看任务状态。 / Decision failed or expired.'
  } finally { busy.value = false }
}
onMounted(poll)
onUnmounted(() => { stopped = true; if (timer) clearTimeout(timer) })
</script>

<style scoped>
pre { max-height: 45vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: 12px; }
p { margin-bottom: 12px; }
</style>
