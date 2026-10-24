import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useI18n } from '@/composables/useI18n'

const mockAppStore = {
  currentLang: 'zh-CN',
  setLang: vi.fn()
}

vi.mock('@/store', () => ({
  useAppStore: vi.fn(() => mockAppStore)
}))

describe('useI18n', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAppStore.currentLang = 'zh-CN'
  })

  afterEach(() => {
    mockAppStore.currentLang = 'zh-CN'
  })

  it('should return Chinese translation by default', () => {
    const { t } = useI18n()
    expect(t('general.name')).toBe('名称')
    expect(t('general.value')).toBe('值')
    expect(t('general.description')).toBe('描述')
  })

  it('should return English translation when language is English', () => {
    mockAppStore.currentLang = 'en'
    const { t } = useI18n()
    expect(t('general.name')).toBe('Name')
    expect(t('general.value')).toBe('Value')
    expect(t('general.description')).toBe('Description')
  })

  it('should support interpolation', () => {
    const { t } = useI18n()
    expect(t('general.saveFailed', { msg: '网络错误' })).toBe('保存失败: 网络错误')
    expect(t('executions.executionCount', { count: 10 })).toBe('10 次执行')
  })

  it('should return fallback for missing keys', () => {
    const { t } = useI18n()
    expect(t('non.existent.key')).toBe('non.existent.key')
    expect(t('non.existent.key', null, 'fallback')).toBe('fallback')
  })

  it('should have all baseline translations', () => {
    const { t } = useI18n()
    expect(t('baselines.title')).toBe('性能基线')
    expect(t('baselines.subtitle')).toBe('管理性能基准和阈值，用于回归分析和漂移检测')
    expect(t('baselines.create')).toBe('创建基线')
    expect(t('baselines.thresholds')).toBe('阈值设置')
    expect(t('baselines.p99Max')).toBe('P99最大响应时间')
  })

  it('should have report comparison translations', () => {
    const { t } = useI18n()
    expect(t('report.compare')).toBe('版本对比')
    expect(t('report.current')).toBe('当前')
    expect(t('report.selectCompare')).toBe('选择对比执行记录')
  })

  it('should set language correctly', () => {
    const { setLang } = useI18n()
    setLang('en')
    expect(mockAppStore.setLang).toHaveBeenCalledWith('en')
  })
})
