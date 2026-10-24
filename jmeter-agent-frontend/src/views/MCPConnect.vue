<template>
  <div class="mcp-page">
    <div class="page-desc">{{ t('mcpConnect.description') }}</div>

    <!-- Server Status -->
    <el-row :gutter="16" class="stats-row">
      <el-col :xs="24" :sm="12" :md="6">
        <el-card class="stat-card">
          <div class="stat-val">{{ status?.tools_count || 0 }}</div>
          <div class="stat-label">{{ t('mcpConnect.stat.tools') }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card class="stat-card">
          <div class="stat-val">{{ status?.resources_count || 0 }}</div>
          <div class="stat-label">{{ t('mcpConnect.stat.resources') }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card class="stat-card">
          <div class="stat-val">{{ status?.prompts_count || 0 }}</div>
          <div class="stat-label">{{ t('mcpConnect.stat.prompts') }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card class="stat-card">
          <div class="stat-val" :class="{ online: connected, offline: !connected }">{{ connected ? t('mcpConnect.status.online') : t('mcpConnect.status.offline') }}</div>
          <div class="stat-label">{{ t('mcpConnect.stat.status') }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Connection Config -->
    <el-card class="config-card">
      <template #header><span><el-icon class="header-icon"><Connection /></el-icon> {{ t('mcpConnect.config') }}</span><el-tag size="small" type="success" style="margin-left:8px">MCP 2024-11-05</el-tag></template>

      <el-alert type="info" :closable="false" style="margin-bottom:16px">
        <template #title>{{ currentLang === 'en' ? 'Legacy HTTP+SSE (2024-11-05); the client must support SSE. Local no-login mode: no access token is needed, and the server only listens on this machine.' : '旧版 HTTP+SSE（2024-11-05），客户端须支持 SSE。本地免登录模式：无需访问令牌，服务仅监听本机。' }}</template>
      </el-alert>

      <!-- AI Tool Tabs -->
      <el-tabs v-model="activeTool" type="border-card">
        <!-- Claude Code -->
        <el-tab-pane :label="t('mcpConnect.tools.claude')" name="claude">
          <div class="tab-content">
            <p>{{ currentLang === 'en' ? 'Add this configuration to .mcp.json in the project root:' : '在项目根目录的 .mcp.json 中添加以下配置：' }}</p>
            <div class="code-block">
              <div class="code-header"><span>.mcp.json</span><el-button class="copy-btn" text size="small" @click="copyConfig('claude')"><el-icon><CopyDocument/></el-icon> {{ t('mcpConnect.copy') }}</el-button></div>
<pre><code>{{ getConfig('claude') }}</code></pre>
            </div>
            <p class="hint">{{ t('mcpConnect.claude.hint') }}</p>
          </div>
        </el-tab-pane>

        <!-- Cursor -->
        <el-tab-pane :label="t('mcpConnect.tools.cursor')" name="cursor">
          <div class="tab-content">
            <p>{{ t('mcpConnect.cursor.instruction') }}</p>
            <div class="code-block">
              <div class="code-header"><span>.cursor/mcp.json</span><el-button class="copy-btn" text size="small" @click="copyConfig('cursor')"><el-icon><CopyDocument/></el-icon> {{ t('mcpConnect.copy') }}</el-button></div>
<pre><code>{{ getConfig('cursor') }}</code></pre>
            </div>
            <p class="hint">{{ t('mcpConnect.cursor.hint') }}</p>
          </div>
        </el-tab-pane>

        <!-- VS Code / Copilot -->
        <el-tab-pane :label="t('mcpConnect.tools.vscode')" name="vscode">
          <div class="tab-content">
            <p>{{ currentLang === 'en' ? 'Add this configuration to .vscode/mcp.json:' : '在 .vscode/mcp.json 中添加以下配置：' }}</p>
            <div class="code-block">
              <div class="code-header"><span>.vscode/mcp.json</span><el-button class="copy-btn" text size="small" @click="copyConfig('vscode')"><el-icon><CopyDocument/></el-icon> {{ t('mcpConnect.copy') }}</el-button></div>
<pre><code>{{ getConfig('vscode') }}</code></pre>
            </div>
          </div>
        </el-tab-pane>

        <!-- Trae IDE -->
        <el-tab-pane :label="t('mcpConnect.tools.trae')" name="trae">
          <div class="tab-content">
            <p>{{ t('mcpConnect.trae.instruction') }}</p>
            <div class="code-block">
              <div class="code-header"><span>mcp.json</span><el-button class="copy-btn" text size="small" @click="copyConfig('trae')"><el-icon><CopyDocument/></el-icon> {{ t('mcpConnect.copy') }}</el-button></div>
<pre><code>{{ getConfig('trae') }}</code></pre>
            </div>
            <p class="hint">{{ t('mcpConnect.trae.hint') }}</p>
          </div>
        </el-tab-pane>

        <!-- Generic / cURL -->
        <el-tab-pane :label="t('mcpConnect.tools.generic')" name="generic">
          <div class="tab-content">
            <p>{{ t('mcpConnect.generic.instruction') }}</p>
            <div class="code-block">
              <div class="code-header"><span>{{ t('mcpConnect.curlTest') }}</span><el-button class="copy-btn" text size="small" @click="copyConfig('generic')"><el-icon><CopyDocument/></el-icon> {{ t('mcpConnect.copy') }}</el-button></div>
<pre><code>{{ getConfig('generic') }}</code></pre>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>

      <!-- Quick Copy URL -->
      <div class="url-bar">
        <span class="url-label">{{ t('mcpConnect.urlLabel') }}</span>
        <el-input class="url-input" :model-value="mcpUrl" readonly size="small"/>
        <el-button class="url-btn" size="small" @click="copyUrl">{{ t('mcpConnect.copyUrl') }}</el-button>
        <el-button class="url-btn" size="small" type="primary" @click="testConnection" :loading="testing">{{ t('mcpConnect.testConnection') }}</el-button>
      </div>
    </el-card>

    <!-- Tool Catalog -->
    <el-card v-if="status?.tools?.length" class="tools-card">
      <template #header><span><el-icon class="header-icon"><Tools /></el-icon> {{ t('mcpConnect.toolCatalog', { count: status.tools.length }) }}</span></template>
      <el-table :data="status.tools" size="small" max-height="500" stripe>
        <el-table-column prop="name" :label="t('mcpConnect.table.name')" width="220" sortable/>
        <el-table-column prop="description" :label="t('mcpConnect.table.description')" min-width="300" show-overflow-tooltip/>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '@/api'
import { ElMessage } from 'element-plus'
import { Connection, CopyDocument, Tools } from '@element-plus/icons-vue'
import { useI18n } from '@/composables/useI18n'

const { t, currentLang } = useI18n()

const status = ref<any>(null)
const connected = ref(false)
const testing = ref(false)
const activeTool = ref('claude')

const origin = computed(() => location.origin)
const mcpUrl = computed(() => `${origin.value}/api/v1/mcp/sse`)

async function loadStatus() {
  try {
    const res = await api.instance.get('/mcp/status')
    status.value = res.data
    connected.value = true
  } catch {
    connected.value = false
  }
}

function getConfig(tool: string): string {
  const base = origin.value
  if (tool === 'generic') {
    return `# Terminal 1: keep this SSE connection open.
curl -N "${base}/api/v1/mcp/sse"

# Terminal 2: copy the session_id from the endpoint event above.
SESSION_ENDPOINT="${base}/api/v1/mcp/messages?session_id=SESSION_ID_FROM_ENDPOINT"
mcp_post() {
  curl -f -X POST "$SESSION_ENDPOINT" -H "Content-Type: application/json" -d "$1"
}
mcp_post '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'

# Wait for initialize result in Terminal 1, then mark the client ready.
mcp_post '{"jsonrpc":"2.0","method":"notifications/initialized"}'
mcp_post '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
# POST returns 202 with no body. Results appear only in Terminal 1.`
  }
  const entry = {
    ...(tool === 'cursor' ? {} : { type: 'sse' }),
    url: mcpUrl.value,
  }
  return JSON.stringify({ [tool === 'vscode' ? 'servers' : 'mcpServers']: { 'jmeter-agent': entry } }, null, 2)
}

async function copyConfig(tool: string) {
  try {
    await navigator.clipboard.writeText(getConfig(tool))
    ElMessage.success(t('mcpConnect.message.copySuccess'))
  } catch {
    // Fallback
    const ta = document.createElement('textarea')
    ta.value = getConfig(tool)
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    ElMessage.success(t('mcpConnect.message.copySuccess'))
  }
}

async function copyUrl() {
  try {
    await navigator.clipboard.writeText(mcpUrl.value)
    ElMessage.success(t('mcpConnect.message.urlCopied'))
  } catch {
    ElMessage.warning(t('mcpConnect.message.copyFailed'))
  }
}

let stopConnectionTest: (() => void) | null = null

async function testConnection() {
  testing.value = true
  try {
    const serverInfo = await new Promise<{ name: string; version: string }>((resolve, reject) => {
      const stream = new EventSource(mcpUrl.value, { withCredentials: true })
      let endpoint = ''
      let info: { name: string; version: string }
      let settled = false
      const finish = (error?: Error) => {
        if (settled) return
        settled = true
        clearTimeout(timeout)
        stream.close()
        stopConnectionTest = null
        if (error) reject(error)
        else resolve(info)
      }
      const timeout = setTimeout(() => finish(new Error('MCP handshake timed out')), 15000)
      stopConnectionTest = () => finish(new Error('MCP connection closed'))
      stream.onerror = () => finish(new Error('MCP SSE connection failed'))
      stream.addEventListener('endpoint', async (event: MessageEvent) => {
        try {
          const url = new URL(event.data, origin.value)
          if (url.origin !== origin.value || !url.searchParams.has('session_id')) {
            throw new Error('Invalid MCP session endpoint')
          }
          endpoint = url.href
          await api.instance.post(endpoint, {
            jsonrpc: '2.0', id: 'init', method: 'initialize',
            params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'web-ui', version: '1.0' } },
          })
        } catch (error) {
          finish(error instanceof Error ? error : new Error('MCP initialize failed'))
        }
      })
      stream.addEventListener('message', async (event: MessageEvent) => {
        try {
          const response = JSON.parse(event.data)
          if (response.error) throw new Error(response.error.message)
          if (response.id === 'init' && response.result?.serverInfo) {
            info = response.result.serverInfo
            await api.instance.post(endpoint, { jsonrpc: '2.0', method: 'notifications/initialized' })
            await api.instance.post(endpoint, { jsonrpc: '2.0', id: 'probe', method: 'ping' })
          } else if (response.id === 'probe' && response.result) {
            finish()
          }
        } catch (error) {
          finish(error instanceof Error ? error : new Error('Invalid MCP response'))
        }
      })
    })
    connected.value = true
    ElMessage.success(t('mcpConnect.message.connectionSuccess', serverInfo))
  } catch (error) {
    connected.value = false
    ElMessage.error(t('mcpConnect.message.connectionFailed', { message: error instanceof Error ? error.message : '' }))
  } finally {
    testing.value = false
  }
}

onUnmounted(() => stopConnectionTest?.())
onMounted(loadStatus)
</script>

<style scoped lang="scss">
.mcp-page {
  max-width: 960px;
  margin: 0 auto;

  @media (max-width: 768px) {
    padding: 0 16px;
    box-sizing: border-box;
  }

  .page-desc { color: var(--tavily-text-muted); margin-bottom: 20px; font-size: 14px; }

  .header-icon {
    font-size: 18px;
    vertical-align: middle;
    margin-right: 2px;
    color: var(--tavily-text-secondary);
  }

  .stats-row { margin-bottom: 20px; }
  .stat-card { text-align: center; border-radius: var(--tavily-radius-xl);
    .stat-val { font-size: 28px; font-weight: 700; color: var(--tavily-text-primary);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      &.online { color: #16A34A; } &.offline { color: #DC2626; }
    }
    .stat-label { font-size: 12px; color: var(--tavily-text-muted); margin-top: 4px; }
  }

  .config-card { margin-bottom: 20px; border-radius: var(--tavily-radius-xl);
    .tab-content { padding: 4px 0;
      p { font-size: 13px; color: var(--tavily-text-secondary); margin: 8px 0; }
      .hint { font-size: 12px; color: var(--tavily-text-muted); margin-top: 12px;
        code { background: var(--tavily-bg-card); padding: 1px 5px; border-radius: var(--radius-sm); font-size: 11px; }
      }
    }

    .el-tabs {
      :deep(.el-tabs__nav) {
        flex-wrap: wrap;
      }

      @media (max-width: 768px) {
        :deep(.el-tabs__item) {
          padding: 0 12px;
        }
      }
    }
  }

  .code-block {
    background: #1e1e2e; border-radius: var(--tavily-radius-lg); overflow: hidden; margin: 8px 0;
    .code-header { display: flex; justify-content: space-between; align-items: center;
      padding: 8px 14px; background: #2d2d3d; color: #cdd6f4; font-size: 12px;
      .copy-btn { width: auto; flex-shrink: 0; }
    }
    pre { margin: 0; padding: 14px; overflow-x: auto;
      code { color: #a6e3a1; font-size: 12px; line-height: 1.6; font-family: 'JetBrains Mono', 'Fira Code', monospace; }
    }
  }

  .url-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 16px; padding: 12px;
    background: var(--tavily-bg-card); border-radius: var(--tavily-radius-lg); border: 1px solid var(--tavily-border);
    .url-label { font-size: 13px; font-weight: 600; white-space: nowrap; color: var(--tavily-text-secondary); flex-shrink: 0; }
    .url-input { flex: 1; min-width: 120px; max-width: 100%; }
    .url-btn { width: auto; flex-shrink: 0; }
  }

  .tools-card { margin-bottom: 20px; border-radius: var(--tavily-radius-xl); }

  @media (max-width: 768px) {
    .el-tabs {
      :deep(.el-tabs__item) {
        padding: 0 8px;
        font-size: 12px;
      }

      :deep(.el-tabs__nav-wrap::after) {
        display: none;
      }
    }

    .url-bar {
      flex-direction: column;
      align-items: stretch;
      gap: 10px;

      .url-label {
        white-space: normal;
      }

      .url-input {
        min-width: 0;
      }

      .url-btn {
        width: 100%;
        justify-content: center;
      }
    }
  }
}
</style>
