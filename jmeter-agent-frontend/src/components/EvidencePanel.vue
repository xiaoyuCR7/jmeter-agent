<template>
  <el-card class="evidence-panel">
    <template #header>
      <div class="panel-header">
        <span>{{ L('判定与证据', 'Verdict & evidence') }}</span>
        <el-tag v-if="req && req.verdict !== 'none'" :type="verdictType" size="large" effect="dark">{{ verdictText }}</el-tag>
      </div>
    </template>

    <div class="section-header"><h4>{{ L('性能需求达标', 'Requirements') }}</h4></div>
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

    <section class="server">
      <div class="section-header">
        <h4>{{ L('服务端指标', 'Server metrics') }}</h4>
        <el-button v-if="server && server.status !== 'not_configured'" text size="small" :loading="refreshing" @click="refresh">
          {{ L('重新采集', 'Re-collect') }}
        </el-button>
      </div>
      <div v-if="!server || server.status === 'not_configured'" class="empty">
        <p>{{ L('未配置 Prometheus，AI 只能根据客户端数据推测根因。', 'Prometheus is not configured, so AI can only guess root causes from client-side data.') }}</p>
        <el-button type="primary" text @click="$router.push({ name: 'ProjectSettings', params: { projectId }, query: { tab: 'monitoring' } })">
          {{ L('配置服务端监控 →', 'Configure server monitoring →') }}
        </el-button>
      </div>
      <el-alert v-else-if="server.status === 'error'" type="error" :closable="false" :title="L('采集失败：', 'Collection failed: ') + (server.error || '')" />
      <template v-else>
        <ul v-if="server.findings?.length" class="findings">
          <li v-for="(f, i) in server.findings" :key="i">{{ f }}</li>
        </ul>
        <el-table :data="server.metrics" size="small">
          <el-table-column :label="L('指标', 'Metric')" min-width="170">
            <template #default="{ row }">
              <div>{{ row.name }}</div>
              <div v-if="row.series && row.series !== 'all'" class="sub">{{ row.series }}</div>
            </template>
          </el-table-column>
          <el-table-column :label="L('平均 / P95 / 峰值', 'Avg / P95 / Peak')" min-width="190">
            <template #default="{ row }">
              <span v-if="row.error" class="bad">{{ row.error }}</span>
              <span v-else>{{ row.avg }} / {{ row.p95 }} / <b>{{ row.max }}</b>{{ row.unit }} <span class="sub">@{{ row.peak_at_s }}s</span></span>
            </template>
          </el-table-column>
          <el-table-column :label="L('饱和时间', 'Saturated')" width="110" align="right">
            <template #default="{ row }">
              <span v-if="row.above_saturation_pct == null" class="muted">—</span>
              <span v-else :class="{ bad: row.above_saturation_pct > 0 }">{{ row.above_saturation_pct }}%</span>
            </template>
          </el-table-column>
          <el-table-column :label="L('与响应时间相关', 'Corr. with RT')" width="130" align="right">
            <template #default="{ row }">
              <span v-if="row.corr_response_time == null" class="muted">—</span>
              <span v-else :class="{ bad: row.corr_response_time >= 0.7 }">{{ row.corr_response_time }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="server.chart?.length" class="chart-block">
          <el-select v-model="chartIndex" size="small" class="chart-select">
            <el-option v-for="(c, i) in server.chart" :key="i" :label="c.series !== 'all' ? `${c.name} · ${c.series}` : c.name" :value="i" />
          </el-select>
          <v-chart class="chart" :option="chartOption" autoresize />
        </div>
      </template>
    </section>
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { api, type ExecutionEvidence } from '@/api'
import { useI18n } from '@/composables/useI18n'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const props = defineProps<{
  evidence: ExecutionEvidence | null
  slaResult?: any
  projectId: string
  timeline?: { time_range: string; avg_response_time: number }[]
}>()
const emit = defineEmits<{ updated: [evidence: ExecutionEvidence] }>()
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

const server = computed(() => props.evidence?.server_metrics ?? null)
const chartIndex = ref(0)
watch(server, () => { chartIndex.value = 0 })
const refreshing = ref(false)

async function refresh() {
  if (!props.evidence) return
  refreshing.value = true
  try {
    emit('updated', (await api.refreshServerMetrics(props.evidence.execution_id)).data)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || L('重新采集失败', 'Re-collection failed'))
  } finally {
    refreshing.value = false
  }
}

// Plot the chosen server metric against JMeter's 30 s response-time segments on one time axis.
const chartOption = computed(() => {
  const series = server.value?.chart?.[chartIndex.value]
  if (!series) return {}
  const rt = (props.timeline || []).map(s => [Number(s.time_range.split('-')[0]) + 15, s.avg_response_time])
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: [series.name, L('平均响应时间', 'Avg response time')] },
    grid: { left: 50, right: 50, top: 36, bottom: 30 },
    xAxis: { type: 'value', name: 's', min: 'dataMin', max: 'dataMax' },
    yAxis: [{ type: 'value', name: series.unit }, { type: 'value', name: 'ms', splitLine: { show: false } }],
    series: [
      { name: series.name, type: 'line', showSymbol: false, data: series.points },
      { name: L('平均响应时间', 'Avg response time'), type: 'line', yAxisIndex: 1, data: rt },
    ],
  }
})
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
.server { margin-top: 24px; border-top: 1px solid var(--tavily-border); padding-top: 16px; }
.section-header { display: flex; justify-content: space-between; align-items: center; }
.section-header h4 { margin: 0 0 10px; font-size: 15px; }
.findings { margin: 0 0 12px; padding-left: 18px; color: var(--el-color-danger); font-size: 13px; }
.chart-block { margin-top: 12px; }
.chart-select { width: 320px; max-width: 100%; }
.chart { height: 280px; margin-top: 8px; }
:deep(.row-fail) td { background: color-mix(in srgb, var(--el-color-danger) 6%, transparent); }
</style>
