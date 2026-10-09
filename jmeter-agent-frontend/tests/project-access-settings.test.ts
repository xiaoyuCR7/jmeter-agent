// @vitest-environment node
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { expect, it, vi } from 'vitest'

const view = readFileSync(new URL('../src/views/ProjectSettings.vue', import.meta.url), 'utf8')
const script = view.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)![1]
function selected(source: string, names: string[], context: Record<string, any>) {
  const ast = ts.createSourceFile('fixture.ts', source, ts.ScriptTarget.Latest, true)
  const statements = ast.statements.filter(node =>
    (ts.isFunctionDeclaration(node) && node.name && names.includes(node.name.text)) ||
    (ts.isVariableStatement(node) && node.declarationList.declarations.some(d => ts.isIdentifier(d.name) && names.includes(d.name.text))))
  const code = statements.map(node => node.getText(ast).replace(/^export\s+/, '')).join('\n')
  return runInNewContext(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText + `\n({${names.join(',')}})`, context)
}
const ref = (value: any) => ({ value })
const computed = (fn: () => any) => ({ get value() { return fn() } })

it('unknown project access remains read only', () => {
  const state = selected(script, ['projectAccess', 'canWrite', 'canManage'], { ref, computed })
  expect(state.canWrite.value).toBe(false)
  expect(state.canManage.value).toBe(false)
  state.projectAccess.value = { permissions: { read: true, write: false, manage: false } }
  expect(state.canWrite.value).toBe(false)
})

it('read-only action handlers cannot submit a project mutation', async () => {
  const api = { updateProject: vi.fn() }
  await selected(script, ['saveProjectInfo'], { api, canWrite: ref(false), savingInfo: ref(false),
    projectId: ref('private'), projectForm: { name: 'name', description: '' },
    ElMessage: { success: vi.fn(), error: vi.fn() }, t: (s: string) => s }).saveProjectInfo()
  expect(api.updateProject).not.toHaveBeenCalled()
})

it('preview never commits and confirmation applies the exact preview mapping', async () => {
  const change = { owner_id: 7, members: { '8': 'viewer' } }
  const preview = { project_id: 'p', revision: 'v1', current: {}, proposed: { ...change, mode: 'private', version: 2 }, resource_counts: {} }
  const context = { api: { previewProjectAccess: vi.fn().mockResolvedValue({ data: preview }),
      applyProjectAccess: vi.fn().mockResolvedValue({}) },
    projectId: ref('p'), canManage: ref(true), accessBusy: ref(false), accessPreview: ref(null),
    buildAccessChange: () => change, loadAccess: vi.fn(), aclText: (zh: string) => zh, t: (key: string) => key,
    ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
    ElMessageBox: { confirm: vi.fn().mockResolvedValue('confirm') } }
  const functions = selected(script, ['previewAccess', 'applyAccess'], context)
  await functions.previewAccess()
  expect(context.api.applyProjectAccess).not.toHaveBeenCalled()
  await functions.applyAccess()
  expect(context.ElMessageBox.confirm).toHaveBeenCalledOnce()
  expect(context.api.applyProjectAccess).toHaveBeenCalledWith('p', { ...change, expected_revision: 'v1' })
})

it('cancelled confirmation leaves access unchanged', async () => {
  const context = { api: { applyProjectAccess: vi.fn() }, projectId: ref('p'), canManage: ref(true),
    accessBusy: ref(false), accessPreview: ref({ revision: 'v', proposed: { owner_id: 1, members: {} } }),
    aclText: (zh: string) => zh, t: (key: string) => key, ElMessageBox: { confirm: vi.fn().mockRejectedValue('cancel') },
    ElMessage: { error: vi.fn(), success: vi.fn() }, loadAccess: vi.fn() }
  await selected(script, ['applyAccess'], context).applyAccess()
  expect(context.api.applyProjectAccess).not.toHaveBeenCalled()
})

it('API management methods preserve encoded project identity and explicit revision', () => {
  const source = readFileSync(new URL('../src/api/index.ts', import.meta.url), 'utf8')
  const instance = { get: vi.fn(), post: vi.fn() }
  const { api } = selected(source, ['api'], { instance, encodeURIComponent })
  api.getProjectAccess('p/other')
  expect(instance.get).toHaveBeenCalledWith('/projects/p%2Fother/access')
  const change = { owner_id: 1, members: { '2': 'viewer' } }
  api.previewProjectAccess('p', change)
  expect(instance.post).toHaveBeenLastCalledWith('/projects/p/access/preview', change)
  api.applyProjectAccess('p', { ...change, expected_revision: 'version' })
  expect(instance.post).toHaveBeenLastCalledWith('/projects/p/access/apply', { ...change, expected_revision: 'version' })
})
