import { defineStore } from 'pinia'
import { ref } from 'vue'
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'
import enLocale from 'element-plus/dist/locale/en.mjs'

export interface EnvironmentInfo {
  id: string
  name: string
  base_url: string
  variables: Record<string, string>
  headers: Record<string, string>
}

const THEME_STORAGE_KEY = 'jmeter-agent-theme'
const LANG_STORAGE_KEY = 'jmeter-agent-lang'

export type SupportedLang = 'zh-CN' | 'en'

export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(false)
  const currentTheme = ref<'light' | 'dark'>('light')
  const currentLang = ref<SupportedLang>('zh-CN')
  const elementLocale = ref(langToElementLocale(currentLang.value))

  const toggleSidebar = () => {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  const applyThemeClass = (theme: 'light' | 'dark') => {
    if (theme === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }

  const initTheme = () => {
    const saved = localStorage.getItem(THEME_STORAGE_KEY)
    const theme = saved === 'dark' ? 'dark' : 'light'
    currentTheme.value = theme
    applyThemeClass(theme)
  }

  const setTheme = (theme: 'light' | 'dark') => {
    currentTheme.value = theme
    localStorage.setItem(THEME_STORAGE_KEY, theme)
    applyThemeClass(theme)
  }

  const initLang = () => {
    const saved = localStorage.getItem(LANG_STORAGE_KEY)
    if (saved === 'en' || saved === 'zh-CN') {
      currentLang.value = saved
    }
    elementLocale.value = langToElementLocale(currentLang.value)
  }

  const setLang = (lang: SupportedLang) => {
    currentLang.value = lang
    localStorage.setItem(LANG_STORAGE_KEY, lang)
    elementLocale.value = langToElementLocale(lang)
  }

  return {
    sidebarCollapsed,
    currentTheme,
    currentLang,
    elementLocale,
    toggleSidebar,
    initTheme,
    setTheme,
    initLang,
    setLang,
  }
})

function langToElementLocale(lang: SupportedLang) {
  return lang === 'en' ? enLocale : zhCn
}

export const useEditorStore = defineStore('editor', () => {
  // Currently selected endpoint ID in the editor
  const selectedEndpointId = ref<string | null>(null)

  // Active environment for the current project
  const activeEnvironment = ref<EnvironmentInfo | null>(null)
  const environments = ref<EnvironmentInfo[]>([])
  const activeEnvironmentId = ref<string | null>(null)

  function selectEndpoint(id: string | null) {
    selectedEndpointId.value = id
  }

  function setEnvironments(envs: EnvironmentInfo[]) {
    environments.value = envs
  }

  function setActiveEnvironment(envId: string | null) {
    activeEnvironmentId.value = envId
    const found = environments.value.find(e => e.id === envId)
    activeEnvironment.value = found || null
  }

  return {
    selectedEndpointId,
    activeEnvironment,
    environments,
    activeEnvironmentId,
    selectEndpoint,
    setEnvironments,
    setActiveEnvironment
  }
})
