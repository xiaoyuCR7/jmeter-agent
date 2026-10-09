// @vitest-environment node
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { describe, expect, it, vi } from 'vitest'

// Execute the actual setup functions without loading the unrelated visual editor.
// This catches their async control-flow bugs, not browser rendering or layout.
function setupFunctions(view: string, names: string[], context: Record<string, any>) {
  const file = readFileSync(new URL(`../src/views/${view}.vue`, import.meta.url), 'utf8')
  const script = file.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)![1]
  const ast = ts.createSourceFile('setup.ts', script, ts.ScriptTarget.Latest, true)
  const source = ast.statements.filter(node =>
    (ts.isFunctionDeclaration(node) && node.name && names.includes(node.name.text)) ||
    (ts.isVariableStatement(node) && node.declarationList.declarations.some(d =>
      ts.isIdentifier(d.name) && names.includes(d.name.text)))
  ).map(node => node.getText(ast)).join('\n')
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  return runInNewContext(`${js}\n({${names.join(',')}})`, context)
}

const ref = (value: any) => ({ value })

describe('save before run', () => {
  it.each([true, false])('only executes after a successful save: %s', async saved => {
    const api = { saveTestPlanTree: vi.fn(), executeTest: vi.fn().mockResolvedValue({}) }
    if (saved) api.saveTestPlanTree.mockResolvedValue({})
    else api.saveTestPlanTree.mockRejectedValue(new Error('save failed'))
    const context = { api, saving: ref(false), running: ref(false), planId: ref('plan'),
      planName: ref('name'), treeData: ref(null), buildTreeFromEndpoints: () => ({ children: [] }),
      ElMessage: { success: vi.fn(), error: vi.fn() }, t: (key: string) => key,
      router: { push: vi.fn() } }
    const { handleRun } = setupFunctions('TestCaseEditor', ['handleSave', 'handleRun'], context)
    await handleRun()
    expect(api.executeTest).toHaveBeenCalledTimes(saved ? 1 : 0)
    expect(context.running.value).toBe(false)
  })
})

describe('workflow SSE', () => {
  const terminal = 'data: {"step":"complete","progress":100,"project_id":"p1"}\r\n\r\n'
  it.each([[terminal], [terminal.slice(0, 17), terminal.slice(17)], [terminal.trimEnd()]])(
    'retains the terminal event across chunk boundaries %#', async (...chunks) => {
      const bytes = (chunks as string[]).map(c => new TextEncoder().encode(c))
      const read = vi.fn().mockImplementation(async () => bytes.length
        ? { done: false, value: bytes.shift() } : { done: true })
      const context = workflowContext({ ok: true, body: { getReader: () => ({ read }) } })
      await setupFunctions('AIChat', ['startWorkflow'], context).startWorkflow()
      expect(context.workflowResult.value?.project_id).toBe('p1')
      expect(context.workflowRunning.value).toBe(false)
      expect(context.ElMessage.error).not.toHaveBeenCalled()
    })

  it('reports incomplete EOF rather than staying running', async () => {
    const context = workflowContext({ ok: true, body: { getReader: () => ({ read: async () => ({ done: true }) }) } })
    await setupFunctions('AIChat', ['startWorkflow'], context).startWorkflow()
    expect(context.workflowRunning.value).toBe(false)
    expect(context.workflowResult.value).toBe(null)
    expect(context.ElMessage.error).toHaveBeenCalledOnce()
  })

  it('rejects an HTTP error before consuming its body', async () => {
    const getReader = vi.fn()
    const context = workflowContext({ ok: false, status: 401, body: { getReader } })
    await setupFunctions('AIChat', ['startWorkflow'], context).startWorkflow()
    expect(getReader).not.toHaveBeenCalled()
    expect(context.ElMessage.error).toHaveBeenCalledOnce()
  })
})

function workflowContext(response: any) {
  return { api: { runAIWorkflowStream: vi.fn().mockResolvedValue(response) },
    workflowApiDoc: ref('api doc'), workflowRequirements: ref('test'), selectedModel: ref(''),
    workflowRunning: ref(false), workflowLoading: ref(false), workflowSteps: ref([]), workflowResult: ref(null),
    ElMessage: { error: vi.fn(), warning: vi.fn() }, TextDecoder, t: (key: string) => key }
}

it('does not start resource polling after unmount while model lookup is pending', async () => {
  const file = readFileSync(new URL('../src/views/Layout.vue', import.meta.url), 'utf8')
  const script = file.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)![1]
  const ast = ts.createSourceFile('setup.ts', script, ts.ScriptTarget.Latest, true)
  const source = ast.statements.filter(node => !ts.isImportDeclaration(node)).map(node => node.getText(ast)).join('\n')
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
  let mounted!: () => Promise<void>, unmounted = () => {}, resolve!: () => void
  const api = { getModels: () => new Promise<void>(r => { resolve = r }), checkResources: vi.fn() }
  const setInterval = vi.fn()
  runInNewContext(js, { ref, computed: (fn: any) => fn, useRoute: () => ({}), useRouter: () => ({}),
    useI18n: () => ({ t: (s: string) => s }), api, setInterval, clearInterval: vi.fn(),
    onMounted: (fn: any) => { mounted = fn }, onUnmounted: (fn: any) => { unmounted = fn } })
  const pending = mounted()
  unmounted()
  resolve()
  await pending
  expect(setInterval).not.toHaveBeenCalled()
  expect(api.checkResources).not.toHaveBeenCalled()
})
