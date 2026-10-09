<template>
  <div class="autotune-page">
    <div class="page-desc">{{ t('autoTune.subtitle') }}</div>

    <el-card class="config-card">
      <template #header><span>{{ t('autoTune.config') }}</span></template>
      <div class="config-form">
        <div class="form-field">
          <label class="form-label">{{ t('autoTune.plan') }}</label>
          <el-select v-model="form.jmx_path" filterable :placeholder="t('autoTune.planPlaceholder')" style="width:100%">
            <el-option v-for="p in testPlans" :key="p.jmx_path" :label="`${p.name} (${p.project_id})`" :value="p.jmx_path"/>
          </el-select>
        </div>
        <div class="form-row">
          <div class="form-field">
            <label class="form-label">{{ t('autoTune.objective') }}</label>
            <el-select v-model="form.goal" style="width:100%">
              <el-option :label="t('autoTune.objective.maxConcurrency')" value="max_safe_concurrency"/>
              <el-option :label="t('autoTune.objective.maxThroughput')" value="max_throughput"/>
              <el-option :label="t('autoTune.objective.minLatency')" value="min_latency"/>
              <el-option :label="t('autoTune.objective.inflection')" value="find_breakpoint"/>
            </el-select>
          </div>
          <div class="form-field">
            <label class="form-label">{{ t('autoTune.constraint.metric') }}</label>
            <el-select v-model="form.constraint_metric" style="width:100%">
              <el-option label="P99" value="p99_response_time"/>
              <el-option :label="t('autoTune.constraint.avgRT')" value="avg_response_time"/>
              <el-option :label="t('autoTune.constraint.errorRate')" value="error_rate"/>
            </el-select>
          </div>
          <div class="form-field">
            <label class="form-label">{{ t('autoTune.constraint.value') }}</label>
            <div class="constraint-value-wrapper">
              <el-input-number v-model="form.constraint_value" :min="1" :step="100" controls-position="right" class="constraint-input"/>
              <span class="unit-hint">{{ form.constraint_metric === 'error_rate' ? '%' : 'ms' }}</span>
            </div>
          </div>
          <div class="form-field">
            <label class="form-label">{{ t('autoTune.strategy') }}</label>
            <el-select v-model="form.strategy" style="width:100%">
              <el-option :label="t('autoTune.strategy.binary')" value="binary_search"/>
              <el-option :label="t('autoTune.strategy.linear')" value="linear_scan"/>
              <el-option :label="t('autoTune.strategy.ai')" value="llm_guided"/>
            </el-select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-field">
            <label class="form-label">{{ t('autoTune.minConcurrency') }}</label>
            <el-input-number v-model="form.min_concurrency" :min="1" :max="10000" style="width:100%"/>
          </div>
          <div class="form-field">
            <label class="form-label">{{ t('autoTune.maxConcurrency') }}</label>
            <el-input-number v-model="form.max_concurrency" :min="1" :max="50000" style="width:100%"/>
          </div>
          <div class="form-field">
            <label class="form-label">{{ t('autoTune.maxIterations') }}</label>
            <el-input-number v-model="form.max_iterations" :min="3" :max="30" style="width:100%"/>
          </div>
          <div class="form-field button-field">
            <el-button type="primary" @click="runAutoTune" :loading="running" :disabled="!form.jmx_path" class="action-button">
              <el-icon><MagicStick/></el-icon> {{ t('autoTune.start') }}
            </el-button>
          </div>
        </div>
      </div>
    </el-card>

    <el-card v-if="result" class="result-card">
      <template #header><span>{{ t('autoTune.result') }}</span></template>
      <div v-if="result.success" class="result-body">
        <el-result icon="success" :title="t('autoTune.optimalConcurrency', { optimal: result.optimal_concurrency })">
          <template #sub-title>
            <div class="metrics-row">
              <div class="metric" v-for="(v,k) in result.optimal_metrics" :key="k">
                <span class="val">{{ typeof v === 'number' ? v.toFixed(1) : v }}</span>
                <span class="label">{{ k }}</span>
              </div>
            </div>
          </template>
        </el-result>
        <p class="reason">{{ result.convergence_reason }}</p>
        <div class="recommendations" v-if="result.recommendations?.length">
          <h4>{{ t('autoTune.suggestion') }}</h4>
          <ul><li v-for="r in result.recommendations" :key="r">{{ r }}</li></ul>
        </div>
        <h4>{{ t('autoTune.iteration') }}</h4>
        <div class="table-wrapper">
          <el-table :data="result.iterations" size="small">
            <el-table-column prop="iteration" label="#" width="50"/>
            <el-table-column prop="concurrent_users" :label="t('autoTune.iteration.concurrency')" width="80"/>
            <el-table-column prop="constraint_value" :label="t('autoTune.iteration.constraintValue')" width="100">
              <template #default="{row}">{{ row.constraint_value?.toFixed(1) }}</template>
            </el-table-column>
            <el-table-column prop="constraint_met" :label="t('autoTune.iteration.meetsConstraint')" width="90">
              <template #default="{row}">
                <el-icon v-if="row.constraint_met" color="#16A34A"><CircleCheck /></el-icon>
                <el-icon v-else color="#DC2626"><CircleClose /></el-icon>
              </template>
            </el-table-column>
            <el-table-column prop="error" :label="t('autoTune.iteration.error')" min-width="150"/>
          </el-table>
        </div>
      </div>
      <el-alert v-else :title="result.error" type="error" show-icon :closable="false"/>
    </el-card>

    <el-card v-if="running" class="progress-card">
      <template #header><span>{{ t('autoTune.inProgress') }}</span></template>
      <el-progress :percentage="progressPct" :stroke-width="8" :indeterminate="progressPct < 10"/>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { api } from '@/api'
import { useI18n } from '@/composables/useI18n'
import { ElMessage } from 'element-plus'
import { MagicStick, CircleCheck, CircleClose } from '@element-plus/icons-vue'

const { t } = useI18n()

const testPlans = ref<any[]>([])
const running = ref(false)
const progressPct = ref(0)
const result = ref<any>(null)

const form = reactive({
  jmx_path: '',
  goal: 'max_safe_concurrency',
  constraint_metric: 'p99_response_time',
  constraint_value: 500,
  strategy: 'binary_search',
  min_concurrency: 5,
  max_concurrency: 1000,
  max_iterations: 10,
})

onMounted(async () => {
  try {
    const res = await api.getTestPlans()
    testPlans.value = (res.data?.test_plans || res.data || [])
  } catch { testPlans.value = [] }
})

async function runAutoTune() {
  running.value = true
  progressPct.value = 5
  result.value = null
  try {
    const res = await api.runAutoTune({ ...form })
    result.value = res.data
    if (res.data.success) {
      ElMessage.success(t('autoTune.success', { optimal: res.data.optimal_concurrency }))
    } else {
      ElMessage.error(res.data.error || t('autoTune.failed'))
    }
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || t('autoTune.requestFailed'))
  } finally {
    running.value = false
    progressPct.value = 100
  }
}
</script>

<style scoped lang="scss">
.autotune-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 16px;
  
  .page-desc { 
    color: var(--tavily-text-muted); 
    margin-bottom: 16px; 
    font-size: 14px; 
    overflow-wrap: break-word;
    word-break: normal;
  }
  
  .config-card { margin-bottom: 20px; border-radius: var(--tavily-radius-xl); }
  
  .config-form {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  
  .form-row {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
  }
  
  .form-field {
    flex: 1;
    min-width: 160px;
    
    &:nth-child(1) { flex: 1.5; min-width: 200px; }
    &:nth-child(2) { flex: 1; min-width: 160px; }
    &:nth-child(3) { flex: 1; min-width: 140px; }
    &:nth-child(4) { flex: 1; min-width: 140px; }
  }
  
  .form-label {
    display: block;
    margin-bottom: 8px;
    font-size: 13px;
    color: var(--tavily-text-secondary);
    font-weight: 500;
  }
  
  .button-field {
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    min-width: 140px;
    
    .action-button {
      width: 100%;
      justify-content: center;
      min-width: 140px;
    }
  }
  
  .constraint-value-wrapper {
    display: flex;
    align-items: center;
    width: 100%;
    
    .constraint-input {
      flex: 1;
      min-width: 0;
    }
    
    .unit-hint {
      margin-left: 6px;
      color: #999;
      font-size: 12px;
      flex-shrink: 0;
      white-space: nowrap;
    }
  }
  
  :deep(.el-input-number) {
    width: 100%;
  }
  
  :deep(.el-select) {
    width: 100%;
  }
  
  .result-card { margin-bottom: 20px; border-radius: var(--tavily-radius-xl);
    .result-body {
      .metrics-row { display: flex; gap: 20px; flex-wrap: wrap; justify-content: center; margin: 10px 0;
        .metric { text-align: center; min-width: 0;
          .val { font-size: 24px; font-weight: 600; color: var(--tavily-primary); display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .label { font-size: 11px; color: var(--tavily-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        }
      }
      .reason { color: var(--tavily-text-secondary); font-size: 13px; margin: 10px 0; overflow-wrap: break-word; word-break: normal; }
      .recommendations { background: var(--tavily-bg-card); padding: 12px 16px; border-radius: var(--tavily-radius-lg); margin: 10px 0;
        h4 { margin: 0 0 6px; font-size: 14px; }
        ul { margin: 0; padding-left: 18px; overflow-wrap: break-word; word-break: normal; li { font-size: 13px; color: var(--tavily-text-secondary); overflow-wrap: break-word; word-break: normal; } }
      }
    }

    .table-wrapper {
      overflow-x: auto;

      .el-table {
        min-width: 470px;
      }
    }
  }
}
</style>
