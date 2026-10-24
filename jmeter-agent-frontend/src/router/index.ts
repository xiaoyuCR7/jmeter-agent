import { createRouter, createWebHistory } from 'vue-router'
import Layout from '@/views/Layout.vue'

export const PROJECT_TABS = ['overview', 'requirements', 'plans', 'baselines', 'settings'] as const

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: Layout,
      children: [
        { path: '', redirect: '/projects' },
        {
          path: 'projects/:projectId?',
          name: 'Projects',
          component: () => import('@/views/Projects.vue'),
          meta: { titleKey: 'route.projects', nav: '/projects' }
        },
        {
          path: `projects/:projectId/:tab(${PROJECT_TABS.join('|')})`,
          name: 'ProjectTab',
          component: () => import('@/views/Projects.vue'),
          meta: { titleKey: 'route.projects', nav: '/projects' }
        },
        {
          path: 'projects/:projectId/test-plans/create',
          name: 'CreateTestPlan',
          component: () => import('@/views/CreateTestPlan.vue'),
          meta: { titleKey: 'route.createTestPlan', nav: '/projects' }
        },
        {
          path: 'projects/:projectId/editor/:planId',
          name: 'TestCaseEditor',
          component: () => import('@/views/TestCaseEditor.vue'),
          meta: { titleKey: 'route.testCaseEditor', nav: '/projects' }
        },
        {
          path: 'start',
          name: 'StartFromDocument',
          component: () => import('@/views/StartFromDocument.vue'),
          meta: { titleKey: 'route.start', nav: '/projects' }
        },
        {
          path: 'executions',
          name: 'Executions',
          component: () => import('@/views/Executions.vue'),
          meta: { titleKey: 'route.executions', nav: '/executions' }
        },
        {
          // Not /reports/:id: the dev server proxies /reports to the backend's static report files.
          path: 'executions/:id',
          name: 'Report',
          component: () => import('@/views/Report.vue'),
          meta: { titleKey: 'route.report', nav: '/executions' }
        },
        {
          path: 'settings',
          name: 'Settings',
          component: () => import('@/views/Settings.vue'),
          meta: { titleKey: 'route.settings', nav: '/settings' }
        },
        // Old addresses, kept so bookmarks still land somewhere sensible.
        {
          path: 'test-plans',
          redirect: to => to.query.projectId ? `/projects/${to.query.projectId}/plans` : '/projects'
        },
        { path: 'baselines', redirect: '/projects' },
        { path: 'ai-chat', redirect: '/start' },
        { path: 'import', redirect: '/start' },
        { path: 'analysis-reports', redirect: '/executions' },
        { path: 'mcp', redirect: '/settings?tab=mcp' },
        { path: ':pathMatch(.*)*', redirect: '/projects' }
      ]
    }
  ]
})

export default router
