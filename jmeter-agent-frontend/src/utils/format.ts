export function formatTime(time?: string | null): string {
  return time ? new Date(time).toLocaleString() : '-'
}

export function formatDuration(start?: string | null, end?: string | null): string {
  if (!start || !end) return '-'
  const seconds = Math.max(0, Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000))
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m ? `${m}m ${s}s` : `${s}s`
}

export const formatMs = (value?: number | null) => (value == null ? '-' : `${Math.round(value)} ms`)
export const formatPct = (value?: number | null) => (value == null ? '-' : `${value.toFixed(2)}%`)
export const formatTps = (value?: number | null) => (value == null ? '-' : value.toFixed(1))

const TEST_TYPES: Record<string, [string, string]> = {
  baseline: ['基准', 'Baseline'],
  concurrency: ['高并发', 'Concurrency'],
  ramp_up: ['阶梯加压', 'Ramp-up'],
  stress: ['压力', 'Stress'],
  stability: ['稳定性', 'Stability'],
  spike: ['尖峰', 'Spike'],
}

export function testTypeText(type: string | undefined, lang: string): string {
  const names = type ? TEST_TYPES[type] : undefined
  if (!names) return type || '-'
  return lang === 'en' ? names[1] : names[0]
}
