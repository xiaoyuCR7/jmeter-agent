<template>
  <div class="report-page" v-loading="loading">
    <template v-if="report">
      <el-card class="report-header">
        <div class="header-content">
          <div>
            <h2>{{ t('report.title') }} - {{ report.test_plan_name }}</h2>
            <p class="meta">
              {{ t('report.executionId', { id: report.execution_id }) }} | 
              {{ t('report.startTime', { time: formatTime(report.start_time) }) }} | 
              {{ t('report.duration', { duration: formatDuration(report.start_time, report.end_time) }) }}
            </p>
          </div>
          <el-tag :type="report.status === 'completed' ? 'success' : 'danger'" size="large">
            {{ report.status === 'completed' ? t('report.success') : t('report.failed') }}
          </el-tag>
        </div>
      </el-card>

      <EvidencePanel :evidence="report.evidence" :sla-result="report.sla_result" :project-id="reportProjectId"
        :timeline="report.summary?.timeline_segments" @updated="report.evidence = $event" />

      <el-card class="compare-card" v-if="comparisonEnabled">
        <template #header>
          <div class="card-header-flex">
            <span>{{ t('report.compare') }}</span>
            <el-switch v-model="comparisonEnabled" />
          </div>
        </template>
        <div class="compare-controls">
          <el-select
            v-model="compareExecutionId"
            :placeholder="t('report.selectCompare')"
            class="compare-select"
            @change="loadComparisonReport"
          >
            <el-option
              v-for="exec in compareExecutions"
              :key="exec.id"
              :label="`${exec.test_plan_name} - ${formatTime(exec.start_time)}`"
              :value="exec.id"
            />
          </el-select>
          <el-button v-if="compareReport" text @click="compareReport = null">
            {{ t('report.clearCompare') }}
          </el-button>
        </div>

        <div v-if="compareReport" class="compare-content">
          <el-row :gutter="16">
            <el-col :xs="24" :sm="24" :md="12">
              <div class="compare-section">
                <h4>{{ t('report.current') }}</h4>
                <div class="compare-metrics">
                  <div class="compare-metric">
                    <span class="label">{{ t('report.avgResponseTime') }}</span>
                    <span class="value">{{ report.summary.avg_response_time }}ms</span>
                  </div>
                  <div class="compare-metric">
                    <span class="label">{{ t('report.p99') }}</span>
                    <span class="value">{{ report.summary.percentiles.p99 }}ms</span>
                  </div>
                  <div class="compare-metric">
                    <span class="label">{{ t('report.errorRate') }}</span>
                    <span class="value">{{ report.summary.error_rate }}%</span>
                  </div>
                  <div class="compare-metric">
                    <span class="label">{{ t('report.throughput') }}</span>
                    <span class="value">{{ report.summary.throughput }} req/s</span>
                  </div>
                </div>
              </div>
            </el-col>
            <el-col :xs="24" :sm="24" :md="12">
              <div class="compare-section">
                <h4>{{ t('report.compare') }}</h4>
                <div class="compare-metrics">
                  <div class="compare-metric">
                    <span class="label">{{ t('report.avgResponseTime') }}</span>
                    <span class="value">{{ compareReport.summary.avg_response_time }}ms</span>
                    <span class="change" :class="getChangeClass(report.summary.avg_response_time, compareReport.summary.avg_response_time)">
                      {{ getChange(report.summary.avg_response_time, compareReport.summary.avg_response_time) }}
                    </span>
                  </div>
                  <div class="compare-metric">
                    <span class="label">{{ t('report.p99') }}</span>
                    <span class="value">{{ compareReport.summary.percentiles.p99 }}ms</span>
                    <span class="change" :class="getChangeClass(report.summary.percentiles.p99, compareReport.summary.percentiles.p99)">
                      {{ getChange(report.summary.percentiles.p99, compareReport.summary.percentiles.p99) }}
                    </span>
                  </div>
                  <div class="compare-metric">
                    <span class="label">{{ t('report.errorRate') }}</span>
                    <span class="value">{{ compareReport.summary.error_rate }}%</span>
                    <span class="change" :class="getChangeClass(report.summary.error_rate, compareReport.summary.error_rate)">
                      {{ getChange(report.summary.error_rate, compareReport.summary.error_rate) }}
                    </span>
                  </div>
                  <div class="compare-metric">
                    <span class="label">{{ t('report.throughput') }}</span>
                    <span class="value">{{ compareReport.summary.throughput }} req/s</span>
                    <span class="change" :class="getChangeClass(report.summary.throughput, compareReport.summary.throughput, true)">
                      {{ getChange(report.summary.throughput, compareReport.summary.throughput) }}
                    </span>
                  </div>
                </div>
              </div>
            </el-col>
          </el-row>
          <v-chart class="compare-chart" :option="compareChartOption" autoresize />
        </div>
      </el-card>


      <!-- Summary Metrics -->
      <el-row :gutter="12" class="metrics-row">
        <el-col :xs="6" :sm="6" :md="8" :lg="4">
          <el-card class="metric-card">
            <div class="metric-value">{{ report.summary.total_requests }}</div>
            <div class="metric-label">{{ t('report.totalRequests') }}</div>
          </el-card>
        </el-col>
        <el-col :xs="6" :sm="6" :md="8" :lg="4">
          <el-card class="metric-card">
            <div class="metric-value" :class="getResponseTimeClass(report.summary.avg_response_time)">
              {{ report.summary.avg_response_time }}ms
            </div>
            <div class="metric-label">{{ t('report.avgResponseTime') }}</div>
          </el-card>
        </el-col>
        <el-col :xs="6" :sm="6" :md="8" :lg="4">
          <el-card class="metric-card">
            <div class="metric-value" :class="getErrorRateClass(report.summary.error_rate)">
              {{ report.summary.error_rate }}%
            </div>
            <div class="metric-label">{{ t('report.errorRate') }}</div>
          </el-card>
        </el-col>
        <el-col :xs="6" :sm="6" :md="8" :lg="4">
          <el-card class="metric-card">
            <div class="metric-value">{{ report.summary.throughput }}</div>
            <div class="metric-label">{{ t('report.throughput') }}</div>
          </el-card>
        </el-col>
        <el-col :xs="6" :sm="6" :md="8" :lg="4">
          <el-card class="metric-card">
            <div class="metric-value">{{ report.summary.min_response_time }}ms</div>
            <div class="metric-label">{{ t('report.minResponseTime') }}</div>
          </el-card>
        </el-col>
        <el-col :xs="6" :sm="6" :md="8" :lg="4">
          <el-card class="metric-card">
            <div class="metric-value" :class="getResponseTimeClass(report.summary.max_response_time)">
              {{ report.summary.max_response_time }}ms
            </div>
            <div class="metric-label">{{ t('report.maxResponseTime') }}</div>
          </el-card>
        </el-col>
      </el-row>

      <!-- Percentiles Chart -->
      <el-row :gutter="16" class="chart-row">
        <el-col :xs="24" :sm="24" :md="12">
          <el-card>
              <template #header>
                <span>{{ t('report.percentile') }}</span>
              </template>
              <v-chart class="chart" :option="percentileChartOption" autoresize />
            </el-card>
          </el-col>
          <el-col :xs="24" :sm="24" :md="12">
            <el-card>
              <template #header>
                <span>{{ t('report.metricsTitle') }}</span>
              </template>
              <div class="perf-table">
                <div class="perf-row">
                  <span class="perf-label">{{ t('report.p50') }}</span>
                  <span class="perf-value">{{ report.summary.percentiles.p50 }}ms</span>
                </div>
                <div class="perf-row">
                  <span class="perf-label">{{ t('report.p90') }}</span>
                  <span class="perf-value">{{ report.summary.percentiles.p90 }}ms</span>
                </div>
                <div class="perf-row">
                  <span class="perf-label">{{ t('report.p95') }}</span>
                  <span class="perf-value">{{ report.summary.percentiles.p95 }}ms</span>
                </div>
                <div class="perf-row">
                  <span class="perf-label">{{ t('report.p99') }}</span>
                  <span class="perf-value">{{ report.summary.percentiles.p99 }}ms</span>
                </div>
              <div class="perf-row">
                <span class="perf-label">{{ t('report.receiveRate') }}</span>
                <span class="perf-value">{{ formatBytes(report.summary.received_bytes_per_sec) }}/s</span>
              </div>
              <div class="perf-row">
                <span class="perf-label">{{ t('report.sendRate') }}</span>
                <span class="perf-value">{{ formatBytes(report.summary.sent_bytes_per_sec) }}/s</span>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- Timeline Performance Data -->
      <el-card v-if="report.summary?.timeline_segments?.length" class="timeline-card">
        <template #header>
            <div class="card-header-title">
              <el-icon size="18" color="#f59e0b"><Timer /></el-icon>
              <span>{{ t('report.timeSeries') }}</span>
            </div>
          </template>
          <div class="table-wrapper">
            <el-table :data="report.summary.timeline_segments" size="small" stripe class="report-table">
              <el-table-column prop="time_range" :label="t('report.timeSeriesTable.timeRange')" width="100" class-name="col-time-range" />
              <el-table-column prop="request_count" :label="t('report.timeSeriesTable.requests')" width="80" class-name="col-requests" />
              <el-table-column prop="avg_response_time" :label="t('report.timeSeriesTable.avg')" width="110" class-name="col-avg">
                <template #default="{ row }">
                  <span :class="getResponseTimeClass(row.avg_response_time)">{{ row.avg_response_time }}ms</span>
                </template>
              </el-table-column>
              <el-table-column prop="max_response_time" :label="t('report.timeSeriesTable.max')" width="110" class-name="col-max" />
              <el-table-column prop="throughput" :label="t('report.timeSeriesTable.throughput')" width="110" class-name="col-throughput" />
              <el-table-column prop="error_rate" :label="t('report.timeSeriesTable.errorRate')" width="90" class-name="col-error">
              <template #default="{ row }">
                <el-tag size="small" :type="row.error_rate > 5 ? 'danger' : row.error_rate > 0 ? 'warning' : 'success'">
                  {{ row.error_rate }}%
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
          </div>
      </el-card>

      <!-- Error Details -->
      <el-card v-if="report.summary?.error_details?.length" class="error-card">
        <template #header>
            <div class="card-header-title">
              <el-icon size="18" color="#ef4444"><Warning /></el-icon>
              <span>{{ t('report.errorAnalysis') }}</span>
            </div>
          </template>
          <el-table :data="report.summary.error_details" size="small" stripe>
            <el-table-column prop="error_type" :label="t('report.errorType')" min-width="200" show-overflow-tooltip />
            <el-table-column prop="count" :label="t('report.errorCount')" width="90" />
            <el-table-column prop="percentage" :label="t('report.errorRatio')" width="100">
              <template #default="{ row }">
                <el-progress :percentage="row.percentage" :color="row.percentage > 10 ? '#ef4444' : '#f59e0b'" />
              </template>
            </el-table-column>
            <el-table-column :label="t('report.errorExample')" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              {{ row.sample_messages?.[0] || '-' }}
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- AI Analysis -->
      <el-card v-if="report.ai_analysis" class="ai-analysis">
        <template #header>
          <div class="analysis-header">
            <el-icon size="20" color="#f59e0b"><Cpu /></el-icon>
            <span>{{ t('report.aiAnalysis') }}</span>
          </div>
        </template>
        
        <div class="analysis-content">
          <el-alert :title="t('report.evidenceWarning')" type="warning" :closable="false" show-icon />
          <!-- 整体评估 -->
          <div class="analysis-section assessment">
            <h4><el-icon><InfoFilled /></el-icon> {{ t('report.evaluation') }}</h4>
            <p>{{ report.ai_analysis.overall_assessment }}</p>
          </div>

        <el-row :gutter="20">
          <el-col :xs="24" :sm="24" :md="12">
            <div class="analysis-section">
              <h4><el-icon><Search /></el-icon> {{ t('report.keyFindings') }}</h4>
              <ul>
                <li v-for="(item, i) in report.ai_analysis.key_findings" :key="i">{{ item }}</li>
              </ul>
            </div>
          </el-col>
          <el-col :xs="24" :sm="24" :md="12">
            <div class="analysis-section issue-section">
              <h4><el-icon><WarningFilled /></el-icon> {{ t('report.issueLocation') }}</h4>
              <ul>
                <li v-for="(item, i) in report.ai_analysis.specific_issues" :key="i">{{ item }}</li>
              </ul>
            </div>
          </el-col>
        </el-row>

        <div v-if="report.ai_analysis.timeline_analysis" class="analysis-section timeline-section">
          <h4><el-icon><Timer /></el-icon> {{ t('report.timelineAnalysis') }}</h4>
            <p>{{ report.ai_analysis.timeline_analysis }}</p>
          </div>

          <!-- 资源瓶颈 + 错误分析 -->
          <el-row :gutter="20">
            <el-col :xs="24" :sm="24" :md="12">
              <div class="analysis-section">
                <h4><el-icon><Monitor /></el-icon> {{ t('report.resourceBottleneck') }}</h4>
                <ul>
                  <li v-for="(item, i) in report.ai_analysis.resource_bottlenecks" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
            <el-col :xs="24" :sm="24" :md="12">
              <div class="analysis-section">
                <h4><el-icon><CircleCloseFilled /></el-icon> {{ t('report.errorRootCause') }}</h4>
                <ul>
                  <li v-for="(item, i) in report.ai_analysis.error_analysis" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
          </el-row>

          <el-row :gutter="20">
            <el-col :xs="24" :sm="24" :md="12">
              <div class="analysis-section">
                <h4><el-icon><TrendCharts /></el-icon> {{ t('report.bottleneck') }}</h4>
                <ul>
                  <li v-for="(item, i) in report.ai_analysis.performance_bottlenecks" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
            <el-col :xs="24" :sm="24" :md="12">
              <div class="analysis-section">
                <h4><el-icon><Lightning /></el-icon> {{ t('report.optimization') }}</h4>
                <ul>
                  <li v-for="(item, i) in report.ai_analysis.recommendations" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
          </el-row>

          <div v-if="report.ai_analysis.improvement_plan?.length" class="analysis-section improvement-section">
            <h4><el-icon><List /></el-icon> {{ t('report.improvementPlan') }}</h4>
            <el-timeline>
              <el-timeline-item
                v-for="(item, i) in report.ai_analysis.improvement_plan"
                :key="i"
                :type="i === 0 ? 'primary' : i === 1 ? 'warning' : 'info'"
              >
                {{ item }}
              </el-timeline-item>
            </el-timeline>
          </div>

          <div v-if="report.ai_analysis.risk_areas?.length" class="analysis-section risk-section">
            <h4><el-icon><Warning /></el-icon> {{ t('report.riskAreas') }}</h4>
            <ul>
              <li v-for="(item, i) in report.ai_analysis.risk_areas" :key="i">{{ item }}</li>
            </ul>
          </div>
          
          <!-- 结论 -->
          <div class="analysis-section conclusion">
            <h4><el-icon><CircleCheckFilled /></el-icon> {{ t('report.conclusion') }}</h4>
            <p>{{ report.ai_analysis.conclusion }}</p>
          </div>
        </div>
      </el-card>
      
      <el-card v-else class="ai-analysis">
        <template #header>
          <span>{{ t('report.aiTab') }}</span>
        </template>
        <el-empty :description="t('report.aiNotEnabled')" />
      </el-card>

      <el-card v-if="dashboardData" class="dashboard-insights">
        <template #header><span>{{ t('report.dashboardInsight') }}</span></template>
        <div v-if="dashboardData.insights?.length">
          <div v-for="(insight,i) in dashboardData.insights" :key="i" class="insight-item">{{ insight }}</div>
        </div>
        <div v-if="dashboardData.transactions?.length" style="margin-top:12px">
          <h4 style="margin:0 0 8px">{{ t('report.endpointsTitle') }}</h4>
          <el-table :data="dashboardData.transactions" size="small" max-height="300">
            <el-table-column prop="name" :label="t('report.endpointsTable.endpoint')" min-width="150"/>
            <el-table-column prop="sample_count" :label="t('report.endpointsTable.samples')" width="90"/>
            <el-table-column prop="avg_response_time" :label="t('report.endpointsTable.avg')" width="100"/>
            <el-table-column prop="p99_response_time" label="P99(ms)" width="90"/>
            <el-table-column prop="error_pct" :label="t('report.endpointsTable.errorRate')" width="80"/>
            <el-table-column prop="throughput" :label="t('report.endpointsTable.throughput')" width="80"/>
          </el-table>
        </div>
      </el-card>

      <el-card v-if="anomalies" class="anomaly-card">
        <template #header>
          <div class="card-header-flex">
            <span>{{ t('report.anomalyDetection') }}</span>
            <el-tag size="small" :type="anomalies.health_score > 80 ? 'success' : anomalies.health_score > 50 ? 'warning' : 'danger'">{{ t('report.healthScore') }} {{ anomalies.health_score }}</el-tag>
          </div>
        </template>
        <p v-if="anomalies.total_anomalies === 0" style="color:#16A34A">{{ t('report.noAnomaly') }}</p>
        <div v-else>
          <p style="color:#DC2626">{{ t('report.anomalyCount', { count: anomalies.total_anomalies }) }}</p>
          <div v-for="(a,i) in anomalies.anomalies.slice(0,10)" :key="i" class="anomaly-item"
               :class="{ high: a.severity==='high', medium: a.severity==='medium' }">
            <span class="sev-tag">{{ a.severity }}</span>
            {{ a.description }}
          </div>
        </div>
      </el-card>

      <!-- Raw Data Download -->
      <el-card>
        <template #header>
          <span>{{ t('report.rawDataTab') }}</span>
        </template>
        <div class="raw-data">
          <el-button @click="downloadJTL">{{ t('report.downloadJtl') }}</el-button>
          <el-button v-if="report.html_report_path" @click="openHtmlReport">{{ t('report.viewHtml') }}</el-button>
          <el-button type="success" @click="loadDashboardData">{{ t('report.refreshInsights') }}</el-button>
          <el-button type="warning" @click="loadAnomalies">{{ t('report.detectAnomaly') }}</el-button>
        </div>
      </el-card>
    </template>
    
    <el-empty v-else :description="t('report.notFound')" />
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { api } from '@/api'
import EvidencePanel from '@/components/EvidencePanel.vue'
import { useI18n } from '@/composables/useI18n'
import { Warning, CircleCheck } from '@element-plus/icons-vue'

use([CanvasRenderer, BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent])

const { t } = useI18n()

const route = useRoute()
const loading = ref(false)
const report = ref<any>(null)
const reportProjectId = computed(() => report.value?.project_id || 'default')
const dashboardData = ref<any>(null)
const anomalies = ref<any>(null)
const comparisonEnabled = ref(true)
const compareExecutionId = ref('')
const compareExecutions = ref<any[]>([])
const compareReport = ref<any>(null)

const percentileChartOption = computed(() => {
  if (!report.value) return {}
  const p = report.value.summary.percentiles
  return {
    tooltip: { trigger: 'axis' },
    backgroundColor: 'transparent',
    textStyle: { color: '#6b7280' },
    xAxis: {
      type: 'category',
      data: ['P50', 'P90', 'P95', 'P99'],
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: { color: '#6b7280' }
    },
    yAxis: {
      type: 'value',
      name: t('report.responseTimeSeries'),
      nameTextStyle: { color: '#6b7280' },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: { color: '#6b7280' },
      splitLine: { lineStyle: { color: '#f3f4f6' } }
    },
    series: [{
      data: [p.p50, p.p90, p.p95, p.p99],
      type: 'bar',
      itemStyle: {
        color: (params: any) => {
          const colors = ['#16A34A', '#2677FF', '#ff7300', '#DC2626']
          return colors[params.dataIndex]
        },
        borderRadius: [6, 6, 0, 0]
      },
      label: {
        show: true,
        position: 'top',
        formatter: '{c}ms',
        color: '#374151'
      }
    }]
  }
})

const compareChartOption = computed(() => {
  if (!report.value || !compareReport.value) return {}
  const metrics = ['Avg RT', 'P99', 'Error Rate', 'Throughput']
  const current = [
    report.value.summary.avg_response_time,
    report.value.summary.percentiles.p99,
    report.value.summary.error_rate,
    report.value.summary.throughput
  ]
  const compare = [
    compareReport.value.summary.avg_response_time,
    compareReport.value.summary.percentiles.p99,
    compareReport.value.summary.error_rate,
    compareReport.value.summary.throughput
  ]

  return {
    tooltip: { trigger: 'axis' },
    legend: {
      data: [t('report.current'), t('report.compare')],
      top: 0
    },
    backgroundColor: 'transparent',
    grid: { top: 40 },
    xAxis: {
      type: 'category',
      data: metrics,
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: { color: '#6b7280' }
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: { color: '#6b7280' },
      splitLine: { lineStyle: { color: '#f3f4f6' } }
    },
    series: [
      {
        name: t('report.current'),
        type: 'bar',
        data: current,
        itemStyle: { color: '#2677FF', borderRadius: [4, 4, 0, 0] },
        barGap: '10%'
      },
      {
        name: t('report.compare'),
        type: 'bar',
        data: compare,
        itemStyle: { color: '#94A3B8', borderRadius: [4, 4, 0, 0] }
      }
    ]
  }
})

const loadReport = async () => {
  const id = route.params.id as string
  if (!id) return
  
  loading.value = true
  try {
    const res = await api.getReport(id)
    report.value = res.data
    await loadCompareExecutions()
  } catch (e: any) {
    report.value = null
  } finally {
    loading.value = false
  }
}

const loadCompareExecutions = async () => {
  if (!report.value) return
  try {
    const res = await api.getExecutions(report.value.project_id)
    compareExecutions.value = (res.data?.executions || res.data || []).filter(
      (e: any) => e.id !== report.value.execution_id && e.status === 'completed'
    ).slice(0, 20)
  } catch {
    compareExecutions.value = []
  }
}

const loadComparisonReport = async (id: string) => {
  if (!id) return
  try {
    const res = await api.getReport(id)
    compareReport.value = res.data
  } catch {
    compareReport.value = null
  }
}

const getChange = (current: number, compare: number) => {
  if (compare === 0) return 'N/A'
  const percent = ((current - compare) / compare * 100)
  const percentStr = percent.toFixed(1)
  return `${percent > 0 ? '+' : ''}${percentStr}%`
}

const getChangeClass = (current: number, compare: number, higherIsBetter = false) => {
  // No percentage exists against a zero baseline, but any rise from it still counts.
  const percent = compare === 0 ? (current > 0 ? Infinity : 0) : (current - compare) / compare * 100
  if (percent > 10) return higherIsBetter ? 'good' : 'bad'
  if (percent < -10) return higherIsBetter ? 'bad' : 'good'
  return ''
}


const getResponseTimeClass = (time: number) => {
  if (time < 200) return 'good'
  if (time < 500) return 'warning'
  return 'danger'
}

const getErrorRateClass = (rate: number) => {
  if (rate === 0) return 'good'
  if (rate < 1) return 'warning'
  return 'danger'
}

const formatTime = (time: string) => {
  if (!time) return '-'
  return new Date(time).toLocaleString()
}

const formatDuration = (start: string, end: string) => {
  const diff = new Date(end).getTime() - new Date(start).getTime()
  const minutes = Math.floor(diff / 60000)
  const seconds = Math.floor((diff % 60000) / 1000)
  return `${minutes}m ${seconds}s`
}

const formatBytes = (bytes: number) => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

const downloadJTL = () => {
  if (!report.value?.raw_data_path) return
  const path = `/reports/${report.value.execution_id}/result.jtl`
  const a = document.createElement('a')
  a.href = path
  a.download = `result_${report.value.execution_id}.jtl`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

const loadDashboardData = async () => {
  if (!report.value) return
  try {
    const res = await api.getDashboardData(report.value.execution_id)
    dashboardData.value = res.data
  } catch { dashboardData.value = null }
}

const loadAnomalies = async () => {
  if (!report.value) return
  try {
    const res = await api.detectAnomalies(report.value.execution_id)
    anomalies.value = res.data
  } catch { anomalies.value = null }
}

const openHtmlReport = () => {
  if (!report.value?.html_report_path) return
  const path = `/reports/${report.value.execution_id}/html/index.html`
  window.open(path, '_blank')
}

onMounted(async () => {
  await loadReport()
  loadDashboardData()
  loadAnomalies()
})
</script>

<style scoped lang="scss">
.report-page {
  max-width: 1400px;
  margin: 0 auto;

  @media (max-width: 768px) {
    padding: 0 16px;
    box-sizing: border-box;
  }

  .report-header {
    margin-bottom: 18px;

    .header-content {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;

      > div {
        min-width: 0;
      }

      h2 {
        margin: 0 0 6px 0;
        font-size: 22px;
        font-weight: 500;
        color: var(--tavily-text-primary);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .meta {
        color: var(--tavily-text-secondary);
        font-size: 13px;
        margin: 0;
        word-break: break-word;
        overflow-wrap: break-word;
      }
    }
  }

  .metrics-row {
    margin-bottom: 18px;

    .metric-card {
      text-align: center;
      background: var(--tavily-bg-card);
      border: 1px solid var(--tavily-border);
      border-radius: var(--tavily-radius-xl);
      transition: all 0.2s ease;
      height: 100%;

      &:hover {
        border-color: var(--tavily-border-blue);
        box-shadow: var(--tavily-shadow-md);
      }

      :deep(.el-card__body) {
        padding: 18px 12px;
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: center;
      }

      .metric-value {
        font-size: 26px;
        font-weight: 500;
        color: var(--tavily-text-primary);
        margin-bottom: 6px;
        letter-spacing: -0.02em;
        line-height: 1.1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;

        &.good { color: #16A34A; }
        &.warning { color: #D97706; }
        &.danger { color: #DC2626; }
      }

      .metric-label {
        font-size: 12px;
        color: var(--tavily-text-secondary);
        font-weight: 500;
      }
    }
  }

  .chart-row {
    margin-bottom: 18px;

    .chart {
      height: 300px;
    }

    .perf-table {
      .perf-row {
        display: flex;
        justify-content: space-between;
        padding: 12px 0;
        border-bottom: 1px solid var(--tavily-border);

        &:last-child {
          border-bottom: none;
        }

        .perf-label {
          color: var(--tavily-text-secondary);
          font-size: 14px;
        }

        .perf-value {
          font-weight: 500;
          color: var(--tavily-text-primary);
          font-size: 14px;
        }
      }
    }
  }

  .timeline-card,
  .error-card {
    margin-bottom: 18px;

    .card-header-title {
      display: flex;
      align-items: center;
      gap: 8px;
      font-weight: 600;
      font-size: 16px;
      color: var(--tavily-text-primary);
    }
  }

  .ai-analysis {
    margin-bottom: 18px;

    .analysis-header {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 700;
      font-size: 17px;
      color: var(--tavily-text-primary);

      .el-icon {
        color: var(--tavily-primary);
      }
    }

    .analysis-content {
      .analysis-section {
        margin-bottom: 24px;
        word-break: break-word;
        overflow-wrap: break-word;

        h4 {
          font-size: 15px;
          font-weight: 700;
          color: var(--tavily-text-primary);
          margin-bottom: 12px;
          padding-left: 12px;
          border-left: 3px solid var(--tavily-primary);
          display: flex;
          align-items: center;
          gap: 8px;

          .el-icon {
            color: var(--tavily-primary);
            font-size: 16px;
          }
        }

        p {
          line-height: 1.8;
          color: var(--tavily-text-secondary);
          font-size: 14px;
        }

        ul {
          margin: 0;
          padding-left: 20px;

          li {
            line-height: 1.8;
            color: var(--tavily-text-secondary);
            margin-bottom: 6px;
            font-size: 14px;
          }
        }

        &.assessment {
          background: #fffbeb;
          padding: 20px;
          border-radius: var(--tavily-radius-xl);
          border: 1px solid rgba(245, 158, 11, 0.2);
        }

        &.issue-section {
          background: #fef2f2;
          padding: 20px;
          border-radius: var(--tavily-radius-xl);
          border: 1px solid rgba(239, 68, 68, 0.15);

          h4 {
            border-left-color: #ef4444;
            .el-icon { color: #ef4444; }
          }

          li {
            color: #991b1b;
          }
        }

        &.timeline-section {
          background: #eff6ff;
          padding: 20px;
          border-radius: var(--tavily-radius-xl);
          border: 1px solid rgba(59, 130, 246, 0.15);

          h4 {
            border-left-color: #3b82f6;
            .el-icon { color: #3b82f6; }
          }
        }

        &.improvement-section {
          background: #f0fdf4;
          padding: 20px;
          border-radius: var(--tavily-radius-xl);
          border: 1px solid rgba(34, 197, 94, 0.15);

          h4 {
            border-left-color: #22c55e;
            .el-icon { color: #22c55e; }
          }
        }

        &.risk-section {
          background: #fff7ed;
          padding: 20px;
          border-radius: var(--tavily-radius-xl);
          border: 1px solid rgba(249, 115, 22, 0.15);

          h4 {
            border-left-color: #f97316;
            .el-icon { color: #f97316; }
          }
        }

        &.conclusion {
          background: #fffbeb;
          padding: 20px;
          border-radius: var(--tavily-radius-xl);
          border: 1px solid rgba(245, 158, 11, 0.2);

          p {
            color: #92400e;
            font-weight: 600;
            font-size: 15px;
          }
        }
      }
    }
  }

  .raw-data {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;

    .el-button {
      width: auto;
    }

    :deep(.el-button + .el-button) {
      margin-left: 0;
    }
  }
  .dashboard-insights {
    margin-bottom: 18px;
    .insight-item { font-size: 14px; padding: 4px 0; color: var(--tavily-text-secondary); }
  }
  .anomaly-card {
    margin-bottom: 18px;
    .anomaly-item {
      padding: 6px 10px; margin: 4px 0; border-radius: var(--tavily-radius-md); font-size: 13px;
      background: var(--tavily-bg-card); border-left: 3px solid var(--tavily-border);
      word-break: break-word;
      overflow-wrap: break-word;
      .sev-tag { font-size: 11px; padding: 0 6px; border-radius: var(--tavily-radius-sm); margin-right: 6px; font-weight: 600; text-transform: uppercase; }
      &.high { border-left-color: #DC2626; .sev-tag { background: #FEE2E2; color: #DC2626; } }
      &.medium { border-left-color: #D97706; .sev-tag { background: #FEF3C7; color: #D97706; } }
    }
  }

  .compare-card {
    margin-bottom: 18px;
  }

  .compare-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    margin-bottom: 16px;

    .compare-select {
      width: 100%;
      max-width: 350px;
    }

    .el-button {
      width: auto;
    }
  }

  .compare-content {
    .compare-section {
      h4 {
        font-size: 14px;
        font-weight: 600;
        color: var(--tavily-text-primary);
        margin-bottom: 12px;
        padding-bottom: 8px;
        border-bottom: 2px solid var(--tavily-primary);
      }

      .compare-metrics {
        .compare-metric {
          display: flex;
          justify-content: space-between;
          align-items: center;
          flex-wrap: wrap;
          gap: 8px;
          min-width: 0;
          padding: 10px 0;
          border-bottom: 1px solid var(--tavily-border);

          .label {
            color: var(--tavily-text-secondary);
            font-size: 13px;
            min-width: 0;
          }

          .value {
            font-weight: 500;
            color: var(--tavily-text-primary);
            font-size: 14px;
            font-family: 'SF Mono', 'Cascadia Code', monospace;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }

          .change {
            font-size: 12px;
            font-weight: 600;
            margin-left: 8px;
            padding: 2px 6px;
            border-radius: var(--radius-sm);

            &.good {
              color: #16A34A;
              background: #DCFCE7;
            }

            &.bad {
              color: #DC2626;
              background: #FEE2E2;
            }
          }
        }
      }
    }
  }

  .compare-chart {
    height: 250px;
    margin-top: 16px;
  }

  .card-header-flex {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }


  .table-wrapper {
    overflow-x: auto;

    .report-table {
      min-width: 600px;
    }
  }

  @media (max-width: 768px) {
    .col-requests,
    .col-max,
    .col-throughput {
      display: none;
    }

    .col-time-range {
      width: 90px;
    }

    .col-avg {
      width: 90px;
    }

    .col-error {
      width: 80px;
    }
  }
}
</style>
