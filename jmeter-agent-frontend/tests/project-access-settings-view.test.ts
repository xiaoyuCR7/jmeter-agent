import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getProjectAccess: vi.fn(), getProject: vi.fn(), getProjectEnvironments: vi.fn(),
  updateProject: vi.fn(), previewProjectAccess: vi.fn(), applyProjectAccess: vi.fn(),
}))
vi.mock('@/api', () => ({ api: mocks }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { projectId: 'p' } }), useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/composables/useI18n', () => ({ useI18n: () => ({ t: (key: string) => key, currentLang: { value: 'en' } }) }))
vi.mock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() } }))
import ProjectSettings from '../src/views/ProjectSettings.vue'

const passthrough = { template: '<div><slot /></div>' }
const stubs = {
  ElTabs: passthrough, ElTabPane: passthrough, ElCard: passthrough, ElFormItem: passthrough,
  ElIcon: passthrough, ElTag: passthrough, ElAlert: true, ElEmpty: passthrough,
  ElForm: { props: ['disabled'], template: '<fieldset :disabled="disabled"><slot /></fieldset>' },
  ElButton: { props: ['disabled'], emits: ['click'], template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>' },
  ElInput: { props: ['modelValue'], template: '<input :value="modelValue" />' },
  ElInputNumber: { props: ['modelValue'], template: '<input type="number" :value="modelValue" />' },
  ElSelect: passthrough, ElOption: true, ElDialog: true,
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.getProject.mockResolvedValue({ data: { name: 'Project', description: '' } })
  mocks.getProjectEnvironments.mockResolvedValue({ data: { environments: [], active_id: null } })
})

it('mounted page stays disabled before access resolves, then keeps readers read only', async () => {
  let resolve!: (value: any) => void
  mocks.getProjectAccess.mockReturnValue(new Promise(r => { resolve = r }))
  const wrapper = mount(ProjectSettings, { global: { stubs } })
  expect(wrapper.find('fieldset').attributes('disabled')).toBeDefined()
  expect(wrapper.find('input[type="number"]').exists()).toBe(false)
  resolve({ data: { project_id: 'p', mode: 'legacy_shared', permissions: { read: true, write: false, manage: false } } })
  await flushPromises()
  expect(wrapper.find('fieldset').attributes('disabled')).toBeDefined()
  expect(wrapper.find('input[type="number"]').exists()).toBe(false)
  expect(wrapper.text()).toContain('Legacy shared project')
  wrapper.unmount()
})

it('only managers see stable user ID inputs and preview action', async () => {
  mocks.getProjectAccess.mockResolvedValue({ data: { project_id: 'p', mode: 'private', owner_id: 7,
    members: { '8': 'viewer' }, permissions: { read: true, write: true, manage: true } } })
  const wrapper = mount(ProjectSettings, { global: { stubs } })
  await flushPromises()
  expect(wrapper.findAll('input[type="number"]')).toHaveLength(2)
  expect(wrapper.text()).toContain('Preview access changes')
  expect(wrapper.find('fieldset').attributes('disabled')).toBeUndefined()
  expect(mocks.getProjectAccess).toHaveBeenCalledOnce()
  wrapper.unmount()
})

it('management-only administrators do not load private project content', async () => {
  mocks.getProjectAccess.mockResolvedValue({ data: { project_id: 'p', mode: 'private', owner_id: 7,
    members: {}, permissions: { read: false, write: false, manage: true } } })
  const wrapper = mount(ProjectSettings, { global: { stubs } })
  await flushPromises()
  expect(wrapper.find('input[type="number"]').exists()).toBe(true)
  expect(mocks.getProject).not.toHaveBeenCalled()
  expect(mocks.getProjectEnvironments).not.toHaveBeenCalled()
  expect(wrapper.find('fieldset').attributes('disabled')).toBeDefined()
  wrapper.unmount()
})
