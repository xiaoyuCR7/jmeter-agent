<template>
  <div class="analysis-reports-page">
    <div class="page-header">
      <h2><el-icon size="22"><Cpu /></el-icon> <span class="page-title-text">{{ t('analysisReports.title') }}</span></h2>
      <el-select v-model="filterProjectId" :placeholder="t('analysisReports.filterProject')" clearable size="default" class="filter-select" @change="loadReports">
        <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
    </div>

    <div v-loading="loading">
      <template v-if="reports.length > 0">
        <div class="table-wrapper">
          <el-table :data="reports" stripe style="width: 100%" @row-click="openDetail" highlight-current-row class="reports-table">
            <el-table-column prop="execution_id" :label="t('analysisReports.executionId')" width="90" class-name="col-id" />
            <el-table-column prop="project_name" :label="t('analysisReports.project')" width="120" show-overflow-tooltip class-name="col-project" />
            <el-table-column prop="test_plan_name" :label="t('analysisReports.testPlan')" width="140" show-overflow-tooltip class-name="col-test-plan" />
            <el-table-column :label="t('analysisReports.status')" width="90" align="center" class-name="col-status">
              <template #default="{ row }">
                <el-tag :type="row.status === 'completed' ? 'success' : 'danger'" size="small">
                  {{ row.status === 'completed' ? t('analysisReports.completed') : row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="overall_assessment" :label="t('analysisReports.overallAssessment')" min-width="200" show-overflow-tooltip class-name="col-assessment" />
            <el-table-column :label="t('analysisReports.keyFindings')" width="70" align="center" class-name="col-findings">
              <template #default="{ row }">
                <el-tag size="small" type="warning">{{ row.key_findings?.length || 0 }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('analysisReports.action')" min-width="140" align="center" class-name="col-actions">
              <template #default="{ row }">
                <div class="action-buttons">
                  <el-button text type="primary" size="small" @click.stop="openDetail(row)">{{ t('analysisReports.view') }}</el-button>
                  <el-button text type="success" size="small" @click.stop="downloadMd(row)" :disabled="!row.has_markdown">{{ t('analysisReports.download') }}</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>

      <el-empty :description="t('analysisReports.empty')">
        <template #description>
          <p style="color: var(--tavily-text-secondary)">{{ t('analysisReports.emptyHint') }}</p>
        </template>
      </el-empty>
    </div>

    <!-- Detail Dialog -->
    <el-dialog
      v-model="detailVisible"
      :title="detailReport ? t('analysisReports.detailTitle', { id: detailReport.execution_id }) : ''"
      width="900px"
      style="max-width: calc(100vw - 32px);"
      top="3vh"
      destroy-on-close
    >
      <template v-if="detail">
        <div class="detail-content">
          <div class="detail-meta">
            <el-tag size="small" type="info">{{ t('analysisReports.executionId') }}: {{ detail.execution_id }}</el-tag>
            <el-tag size="small" type="success" v-if="detail.has_markdown">{{ t('analysisReports.markdownAvailable') }}</el-tag>
          </div>

          <el-divider />
          <el-alert :title="t('report.evidenceWarning')" type="warning" :closable="false" show-icon />

          <div class="analysis-section assessment">
            <h4><el-icon><InfoFilled /></el-icon> {{ t('analysisReports.overallAssessmentSection') }}</h4>
            <p>{{ detail.ai_analysis.overall_assessment }}</p>
          </div>

          <el-row :gutter="20">
            <el-col :span="12" :xs="24" :sm="24" :md="12">
              <div class="analysis-section">
                <h4><el-icon><Search /></el-icon> {{ t('analysisReports.keyFindingsSection') }}</h4>
                <ul>
                  <li v-for="(item, i) in detail.ai_analysis.key_findings" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
            <el-col :span="12" :xs="24" :sm="24" :md="12">
              <div class="analysis-section issue-section">
                <h4><el-icon><WarningFilled /></el-icon> {{ t('analysisReports.specificIssues') }}</h4>
                <ul>
                  <li v-for="(item, i) in detail.ai_analysis.specific_issues" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
          </el-row>

          <div v-if="detail.ai_analysis.timeline_analysis" class="analysis-section timeline-section">
            <h4><el-icon><Timer /></el-icon> {{ t('analysisReports.timelineAnalysis') }}</h4>
            <p>{{ detail.ai_analysis.timeline_analysis }}</p>
          </div>

          <el-row :gutter="20">
            <el-col :span="12" :xs="24" :sm="24" :md="12">
              <div class="analysis-section">
                <h4><el-icon><Monitor /></el-icon> {{ t('analysisReports.resourceBottlenecks') }}</h4>
                <ul>
                  <li v-for="(item, i) in detail.ai_analysis.resource_bottlenecks" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
            <el-col :span="12" :xs="24" :sm="24" :md="12">
              <div class="analysis-section">
                <h4><el-icon><CircleCloseFilled /></el-icon> {{ t('analysisReports.errorRootCause') }}</h4>
                <ul>
                  <li v-for="(item, i) in detail.ai_analysis.error_analysis" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
          </el-row>

          <el-row :gutter="20">
            <el-col :span="12" :xs="24" :sm="24" :md="12">
              <div class="analysis-section">
                <h4><el-icon><TrendCharts /></el-icon> {{ t('analysisReports.performanceBottlenecks') }}</h4>
                <ul>
                  <li v-for="(item, i) in detail.ai_analysis.performance_bottlenecks" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
            <el-col :span="12" :xs="24" :sm="24" :md="12">
              <div class="analysis-section">
                <h4><el-icon><Lightning /></el-icon> {{ t('analysisReports.recommendations') }}</h4>
                <ul>
                  <li v-for="(item, i) in detail.ai_analysis.recommendations" :key="i">{{ item }}</li>
                </ul>
              </div>
            </el-col>
          </el-row>

          <div v-if="detail.ai_analysis.improvement_plan?.length" class="analysis-section improvement-section">
            <h4><el-icon><List /></el-icon> {{ t('analysisReports.improvementPlan') }}</h4>
            <el-timeline>
              <el-timeline-item
                v-for="(item, i) in detail.ai_analysis.improvement_plan"
                :key="i"
                :type="i === 0 ? 'primary' : i === 1 ? 'warning' : 'info'"
              >
                {{ item }}
              </el-timeline-item>
            </el-timeline>
          </div>

          <div v-if="detail.ai_analysis.risk_areas?.length" class="analysis-section risk-section">
            <h4><el-icon><Warning /></el-icon> {{ t('analysisReports.riskAreas') }}</h4>
            <ul>
              <li v-for="(item, i) in detail.ai_analysis.risk_areas" :key="i">{{ item }}</li>
            </ul>
          </div>

          <div class="analysis-section conclusion">
            <h4><el-icon><CircleCheckFilled /></el-icon> {{ t('analysisReports.conclusion') }}</h4>
            <p>{{ detail.ai_analysis.conclusion }}</p>
          </div>
        </div>
      </template>

      <template #footer>
        <el-button @click="detailVisible = false">{{ t('analysisReports.close') }}</el-button>
        <el-button type="primary" @click="downloadCurrentMd" :disabled="!detail?.has_markdown">
          <el-icon><Download /></el-icon> {{ t('analysisReports.downloadMarkdown') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Cpu, InfoFilled, Search, WarningFilled, Timer, Monitor,
  CircleCloseFilled, TrendCharts, Lightning, List, Warning,
  CircleCheckFilled, Download
} from '@element-plus/icons-vue'
import { api } from '@/api'
import { useI18n } from '@/composables/useI18n'

const { t } = useI18n()

const loading = ref(false)
const reports = ref<any[]>([])
const projects = ref<any[]>([])
const filterProjectId = ref('')
const detailVisible = ref(false)
const detail = ref<any>(null)
const detailReport = ref<any>(null)

const loadReports = async () => {
  loading.value = true
  try {
    const res = await api.listAnalysisReports(filterProjectId.value || undefined)
    reports.value = res.data.reports || []
  } catch {
    reports.value = []
  } finally {
    loading.value = false
  }
}

const loadProjects = async () => {
  try {
    const res = await api.getProjects()
    projects.value = res.data.projects || []
  } catch { projects.value = [] }
}

const openDetail = async (row: any) => {
  detailReport.value = row
  detailVisible.value = true
  try {
    const res = await api.getAnalysisReport(row.execution_id)
    detail.value = res.data
  } catch {
    detail.value = null
    ElMessage.error(t('analysisReports.message.loadFailed'))
  }
}

const downloadMd = async (row: any) => {
  if (!row.has_markdown) return
  try {
    const res = await api.downloadAnalysisReport(row.execution_id)
    const blob = new Blob([res.data], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `ai_analysis_${row.execution_id}.md`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    ElMessage.success(t('analysisReports.message.downloadSuccess'))
  } catch {
    ElMessage.error(t('analysisReports.message.downloadFailed'))
  }
}

const downloadCurrentMd = () => {
  if (detailReport.value) {
    downloadMd(detailReport.value)
  }
}

onMounted(() => {
  loadProjects()
  loadReports()
})
</script>

<style scoped lang="scss">
.analysis-reports-page {
  .page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 20px;
    min-width: 0;

    h2 {
      margin: 0;
      font-size: 22px;
      font-weight: 600;
      color: var(--tavily-text-primary);
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
      flex: 1;

      .el-icon {
        flex-shrink: 0;
      }

      .page-title-text {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    }

    .filter-select {
      width: 240px;
      max-width: 100%;
    }

    @media (max-width: 768px) {
      .filter-select {
        width: 100%;
      }
    }
  }

  :deep(.el-table__row) {
    cursor: pointer;
  }

  .table-wrapper {
    overflow-x: auto;
    border-radius: var(--tavily-radius-xl);
    border: 1px solid var(--tavily-border);
    background: var(--tavily-bg-card);
    padding: 4px;
  }

  .reports-table {
    min-width: 700px;

    @media (max-width: 768px) {
      .col-id,
      .col-project,
      .col-findings,
      .col-assessment {
        display: none;
      }

      .col-test-plan {
        width: 100px;
      }

      .col-status {
        width: 80px;
      }

      .col-actions {
        min-width: 120px;
      }
    }
  }

  .action-buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    justify-content: center;
  }

  .detail-content {
    max-height: calc(90vh - 140px);
    overflow-y: auto;

    .detail-meta {
      display: flex;
      gap: 10px;
    }

    .analysis-section {
      margin-bottom: 20px;

      h4 {
        font-size: 15px;
        font-weight: 700;
        color: var(--tavily-text-primary);
        margin-bottom: 10px;
        padding-left: 12px;
        border-left: 3px solid var(--tavily-primary, #409EFF);
        display: flex;
        align-items: center;
        gap: 8px;
      }

      p {
        line-height: 1.8;
        color: var(--tavily-text-secondary);
        font-size: 14px;
        overflow-wrap: break-word;
        word-break: normal;
      }

      ul {
        margin: 0;
        padding-left: 20px;
        overflow-wrap: break-word;
        word-break: normal;
        li {
          line-height: 1.8;
          color: var(--tavily-text-secondary);
          margin-bottom: 4px;
          font-size: 14px;
          overflow-wrap: break-word;
          word-break: normal;
        }
      }

      &.assessment {
        background: #fffbeb;
        padding: 16px 20px;
        border-radius: var(--tavily-radius-lg);
        border: 1px solid rgba(245, 158, 11, 0.2);
      }
      &.issue-section {
        background: #fef2f2;
        padding: 16px 20px;
        border-radius: var(--tavily-radius-lg);
        border: 1px solid rgba(239, 68, 68, 0.15);
        h4 { border-left-color: #ef4444; }
        li { color: #991b1b; }
      }
      &.timeline-section {
        background: #eff6ff;
        padding: 16px 20px;
        border-radius: var(--tavily-radius-lg);
        border: 1px solid rgba(59, 130, 246, 0.15);
        h4 { border-left-color: #3b82f6; }
      }
      &.improvement-section {
        background: #f0fdf4;
        padding: 16px 20px;
        border-radius: var(--tavily-radius-lg);
        border: 1px solid rgba(34, 197, 94, 0.15);
        h4 { border-left-color: #22c55e; }
      }
      &.risk-section {
        background: #fff7ed;
        padding: 16px 20px;
        border-radius: var(--tavily-radius-lg);
        border: 1px solid rgba(249, 115, 22, 0.15);
        h4 { border-left-color: #f97316; }
      }
      &.conclusion {
        background: #fffbeb;
        padding: 16px 20px;
        border-radius: var(--tavily-radius-lg);
        border: 1px solid rgba(245, 158, 11, 0.2);
        p { color: #92400e; font-weight: 600; font-size: 15px; }
      }
    }
  }
}
</style>
