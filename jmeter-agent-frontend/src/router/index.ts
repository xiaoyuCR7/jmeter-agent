import { createRouter, createWebHistory } from 'vue-router'
import Layout from '@/views/Layout.vue'
import { api } from '@/api'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'Login', component: () => import('@/views/Login.vue') },
    {
      path: '/',
      component: Layout,
      children: [
        {
          path: '',
          redirect: '/test-plans'
        },
        {
          path: 'test-plans',
          name: 'TestPlans',
          component: () => import('@/views/TestPlans.vue'),
          meta: { titleKey: 'route.testPlans' }
        },
        {
          path: 'projects/:projectId/test-plans/create',
          name: 'CreateTestPlan',
          component: () => import('@/views/CreateTestPlan.vue'),
          meta: { titleKey: 'route.createTestPlan' }
        },
        {
          path: 'editor/:id',
          name: 'JmxEditor',
          component: () => import('@/views/JmxEditor.vue'),
          meta: { titleKey: 'route.jmxEditor' }
        },
        {
          path: 'projects/:projectId/editor/:planId',
          name: 'TestCaseEditor',
          component: () => import('@/views/TestCaseEditor.vue'),
          meta: { titleKey: 'route.testCaseEditor' }
        },
        {
          path: 'projects/:projectId/settings',
          name: 'ProjectSettings',
          component: () => import('@/views/ProjectSettings.vue'),
          meta: { titleKey: 'route.projectSettings' }
        },
        {
          path: 'executions',
          name: 'Executions',
          component: () => import('@/views/Executions.vue'),
          meta: { titleKey: 'route.executions' }
        },
        {
          path: 'schedules',
          name: 'Schedules',
          component: () => import('@/views/Schedules.vue'),
          meta: { titleKey: 'route.schedules' }
        },
        {
          path: 'baselines',
          name: 'Baselines',
          component: () => import('@/views/Baselines.vue'),
          meta: { titleKey: 'route.baselines' }
        },
        {
          path: 'reports/:id',
          name: 'Report',
          component: () => import('@/views/Report.vue'),
          meta: { titleKey: 'route.report' }
        },
        {
          path: 'ai-chat',
          name: 'AIChat',
          component: () => import('@/views/AIChat.vue'),
          meta: { titleKey: 'route.aiChat' }
        },
        {
          path: 'autotune',
          name: 'AutoTune',
          component: () => import('@/views/AutoTune.vue'),
          meta: { titleKey: 'route.autoTune' }
        },
        {
          path: 'import',
          name: 'OpenAPIImport',
          component: () => import('@/views/OpenAPIImport.vue'),
          meta: { titleKey: 'route.openApiImport' }
        },
        {
          path: 'mcp',
          name: 'MCPConnect',
          component: () => import('@/views/MCPConnect.vue'),
          meta: { titleKey: 'route.mcpConnect' }
        },
        {
          path: 'analysis-reports',
          name: 'AnalysisReports',
          component: () => import('@/views/AnalysisReports.vue'),
          meta: { titleKey: 'route.analysisReports' }
        },
        {
          path: 'settings',
          name: 'Settings',
          component: () => import('@/views/Settings.vue'),
          meta: { titleKey: 'route.settings' }
        }
      ]
    }
  ]
})

router.beforeEach(async to => {
  if (to.name === 'Login') return true
  try {
    await api.getCurrentUser()
    return true
  } catch {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
})

window.addEventListener('auth-required', () => {
  if (router.currentRoute.value.name !== 'Login') {
    void router.replace({ name: 'Login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})

export default router
