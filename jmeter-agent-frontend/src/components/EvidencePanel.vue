<template>
  <el-card class="evidence-panel">
    <template #header>
      <div class="panel-header">
        <span>{{ L('需求达标判定', 'Requirement verdict') }}</span>
        <el-tag v-if="req && req.verdict !== 'none'" :type="verdictType" size="large" effect="dark">{{ verdictText }}</el-tag>
      </div>
    </template>

    <template v-if="req && req.verdict !== 'none'">
      <p class="tally">
        {{ L('通过', 'Passed') }} <b class="ok">{{ req.passed }}</b> ·
        {{ L('不通过', 'Failed') }} <b class="bad">{{ req.failed }}</b> ·
        {{ L('无法评估', 'No data') }} <b class="muted">{{ req.missing }}</b>
      </p>
      <el-table :data="req.items" size="small" :row-class-name="rowClass">
        <el-table-column :label="L('编号', 'ID')" prop="id" width="70" />
        <el-table-column :label="L('需求', 'Requirement')" min-width="220">
          <template #default="{ row }">
            <div>{{ row.description }}</div>
            <div v-if="row.matched_labels.length > 1" class="sub">{{ L('匹配接口', 'Matched') }}: {{ row.matched_labels.join(', ') }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="L('实际值', 'Actual')" width="120" align="right">
          <template #default="{ row }">
            <span v-if="row.actual === null" class="muted">{{ L('无数据', 'n/a') }}</span>
            <span v-else>{{ format(row.actual) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="L('结果', 'Result')" width="130">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
            <span v-if="row.gap_pct !== null" class="gap">+{{ row.gap_pct }}%</span>
          </template>
        </el-table-column>
        <el-table-column :label="L('文档原文', 'Source')" min-width="220">
          <template #default="{ row }">
            <span class="quote" :class="{ unverified: row.source && !row.source_verified }">{{ row.source || '—' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <div v-else class="empty">
      <p>{{ L('本项目还没有录入性能需求，下面按默认阈值评估。', 'This project has no performance requirements yet; default thresholds are used below.') }}</p>
      <ul v-if="slaResult" class="sla">
        <li v-for="(v, i) in slaResult.violations" :key="i" class="bad">{{ v }}</li>
        <li v-if="!slaResult.violations?.length" class="ok">{{ L('默认阈值全部通过', 'All default thresholds passed') }}</li>
      </ul>
      <el-button type="primary" text @click="$router.push({ name: 'ProjectSettings', params: { projectId }, query: { tab: 'requirements' } })">
        {{ L('从测试文档录入需求 →', 'Add requirements from a test document →') }}
      </el-button>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ExecutionEvidence } from '@/api'
import { useI18n } from '@/composables/useI18n'

const props = defineProps<{ evidence: ExecutionEvidence | null; slaResult?: any; projectId: string }>()
const { currentLang } = useI18n()
const L = (zh: string, en: string) => currentLang.value === 'en' ? en : zh

const req = computed(() => props.evidence?.requirements ?? null)
const verdictType = computed(() => ({ pass: 'success', fail: 'danger', incomplete: 'warning' } as const)[req.value!.verdict as 'pass' | 'fail' | 'incomplete'])
const verdictText = computed(() => ({
  pass: L('全部达标', 'All met'), fail: L('未达标', 'Not met'), incomplete: L('部分无法评估', 'Incomplete'),
} as Record<string, string>)[req.value!.verdict])

const statusType = (s: string) => ({ pass: 'success', fail: 'danger', missing: 'info' } as Record<string, any>)[s]
const statusText = (s: string) => ({ pass: L('达标', 'Pass'), fail: L('不达标', 'Fail'), missing: L('无数据', 'No data') } as Record<string, string>)[s]
const rowClass = ({ row }: { row: { status: string } }) => row.status === 'fail' ? 'row-fail' : ''
const format = (n: number) => Number.isInteger(n) ? String(n) : n.toFixed(2)
</script>

<style scoped lang="scss">
.panel-header { display: flex; justify-content: space-between; align-items: center; font-weight: 500; }
.tally { margin: 0 0 12px; color: var(--tavily-text-secondary); }
.ok { color: var(--el-color-success); }
.bad { color: var(--el-color-danger); }
.muted { color: var(--tavily-text-muted); }
.sub { font-size: 12px; color: var(--tavily-text-muted); margin-top: 2px; }
.gap { margin-left: 6px; font-size: 12px; color: var(--el-color-danger); font-variant-numeric: tabular-nums; }
.quote { font-size: 12px; color: var(--tavily-text-secondary); }
.quote.unverified { color: var(--el-color-warning); }
.empty p { margin: 0 0 8px; color: var(--tavily-text-secondary); }
.sla { margin: 0 0 8px; padding-left: 18px; font-size: 13px; }
:deep(.row-fail) td { background: color-mix(in srgb, var(--el-color-danger) 6%, transparent); }
</style>
