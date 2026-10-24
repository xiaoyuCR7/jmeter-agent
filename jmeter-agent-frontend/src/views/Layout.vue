<template>
  <el-container class="layout-container">
    <ToolConfirmations />
    <div v-if="mobileSidebarVisible" class="mobile-sidebar-overlay" @click="mobileSidebarVisible = false"></div>
    <el-aside 
      :width="sidebarCollapsed ? '60px' : '240px'" 
      class="sidebar" 
      :class="{ collapsed: sidebarCollapsed, 'mobile-open': mobileSidebarVisible }"
    >
      <div class="logo" v-show="!sidebarCollapsed">
        <div class="logo-icon">
          <el-icon size="22"><Connection /></el-icon>
        </div>
        <span class="logo-text">JMeter Agent</span>
      </div>
      <div class="logo-mini" v-show="sidebarCollapsed">
        <el-icon size="22" color="#3c3a39"><Connection /></el-icon>
      </div>
      <el-menu
        :default-active="activeMenu"
        class="sidebar-menu"
        router
        background-color="transparent"
      >
        <el-menu-item index="/test-plans">
          <el-icon><Document /></el-icon>
          <span>{{ t('layout.nav.testPlans') }}</span>
        </el-menu-item>
        <el-menu-item index="/executions">
          <el-icon><VideoPlay /></el-icon>
          <span>{{ t('layout.nav.executions') }}</span>
        </el-menu-item>
        <el-menu-item index="/baselines">
          <el-icon><Aim /></el-icon>
          <span>{{ t('layout.nav.baselines') }}</span>
        </el-menu-item>
        <el-menu-item index="/analysis-reports">
          <el-icon><DataAnalysis /></el-icon>
          <span>{{ t('layout.nav.analysisReports') }}</span>
        </el-menu-item>
        <el-menu-item index="/import">
          <el-icon><Upload /></el-icon>
          <span>{{ t('layout.nav.import') }}</span>
        </el-menu-item>
        <el-menu-item index="/mcp">
          <el-icon><Connection /></el-icon>
          <span>{{ t('layout.nav.mcp') }}</span>
        </el-menu-item>
        <el-menu-item index="/ai-chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>{{ t('layout.nav.aiChat') }}</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>{{ t('layout.nav.settings') }}</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-footer" v-show="!sidebarCollapsed">
        <div class="ai-status" :class="{ connected: aiConnected }">
          <div class="status-dot"></div>
          <span>{{ aiConnected ? t('layout.ai.connected') : t('layout.ai.disconnected') }}</span>
        </div>
      </div>

      <button class="collapse-btn" @click="sidebarCollapsed = !sidebarCollapsed" :title="sidebarCollapsed ? t('layout.expand') : t('layout.collapse')">
        <el-icon :class="{ rotated: sidebarCollapsed }"><ArrowLeft /></el-icon>
      </button>
    </el-aside>

    <el-container class="main-container">
      <el-header class="header">
        <div class="header-left">
          <button class="hamburger-btn" @click="mobileSidebarVisible = !mobileSidebarVisible">
            <el-icon><Menu /></el-icon>
          </button>
          <h2>{{ pageTitle }}</h2>
        </div>
        <div class="header-right">
          <div class="resource-bar" v-if="resources" :title="t('layout.resources')">
            <span class="res-item" :class="{ warn: resources.cpu_percent > 70, danger: resources.cpu_percent > 85 }">
              CPU {{ resources.cpu_percent }}%
            </span>
            <span class="res-item" :class="{ warn: resources.memory_percent > 70, danger: resources.memory_percent > 85 }">
              MEM {{ resources.memory_percent }}%
            </span>
          </div>
        </div>
      </el-header>

      <el-main class="main-content">
        <router-view v-slot="{ Component }">
          <transition name="slide-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api'
import ToolConfirmations from '@/components/ToolConfirmations.vue'
import { ArrowLeft, Menu, Connection, Document, VideoPlay, DataAnalysis, Upload, ChatDotRound, Setting, Aim } from '@element-plus/icons-vue'
import { useI18n } from '@/composables/useI18n'

const route = useRoute()
const { t } = useI18n()
const aiConnected = ref(false)
const sidebarCollapsed = ref(false)
const mobileSidebarVisible = ref(false)
const resources = ref<any>(null)
let resourceTimer: ReturnType<typeof setInterval> | undefined
let disposed = false
let resourceRequestPending = false

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => (route.meta.titleKey ? t(route.meta.titleKey as string) : 'JMeter Agent'))

const fetchResources = async () => {
  if (disposed || resourceRequestPending) return
  resourceRequestPending = true
  try {
    const res = await api.checkResources()
    if (!disposed) resources.value = res.data
  } catch { /* ignore */ }
  finally { resourceRequestPending = false }
}

onMounted(async () => {
  try {
    await api.getModels()
    aiConnected.value = true
  } catch {
    aiConnected.value = false
  }
  if (disposed) return
  fetchResources()
  resourceTimer = setInterval(fetchResources, 15000)
})
onUnmounted(() => {
  disposed = true
  if (resourceTimer !== undefined) clearInterval(resourceTimer)
})
</script>

<style scoped lang="scss">
.layout-container {
  min-height: 100vh;
  background: var(--tavily-bg-gradient);

  .mobile-sidebar-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.3);
    z-index: 999;
    display: none;

    @media (max-width: 768px) {
      display: block;
    }
  }

  .sidebar {
    background: var(--tavily-bg-sidebar);
    border-right: 1px solid var(--tavily-border);
    backdrop-filter: blur(var(--glass-blur-md));
    -webkit-backdrop-filter: blur(var(--glass-blur-md));
    display: flex;
    flex-direction: column;
    position: relative;
    overflow: visible;

    @media (max-width: 768px) {
      position: fixed;
      top: 0;
      left: 0;
      bottom: 0;
      z-index: 1000;
      transform: translateX(-100%);
      transition: transform 0.3s ease;
      box-shadow: var(--tavily-shadow-lg);

      &.mobile-open {
        transform: translateX(0);
      }
    }

    .logo {
      height: 64px;
      display: flex;
      align-items: center;
      padding: 0 20px;
      border-bottom: 1px solid var(--tavily-border);

      .logo-icon {
        width: 36px;
        height: 36px;
        border-radius: var(--tavily-radius-lg);
        background: var(--tavily-bg-card);
        border: 1px solid var(--tavily-border);
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: var(--tavily-shadow-sm);
        backdrop-filter: blur(var(--glass-blur-sm));
        -webkit-backdrop-filter: blur(var(--glass-blur-sm));

        .el-icon {
          color: var(--tavily-text-primary);
        }
      }

      .logo-text {
        margin-left: 12px;
        font-size: 17px;
        font-weight: 500;
        color: var(--tavily-text-primary);
        letter-spacing: -0.01em;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        min-width: 0;
      }
    }

    .sidebar-menu {
      flex: 1;
      overflow-y: auto;
      padding: 12px 8px;
      border-right: none;

      :deep(.el-menu-item) {
        height: 42px;
        line-height: 42px;
        margin: 2px 8px;
        border-radius: var(--tavily-radius-lg);
        font-size: 14px;
        font-weight: 500;
        transition: all 0.15s ease;

        .el-icon {
          font-size: 18px;
          margin-right: 10px;
        }

        &:hover {
          background: var(--tavily-primary-bg) !important;
          color: var(--tavily-text-primary) !important;
        }

        &.is-active {
          background: var(--tavily-primary) !important;
          color: #fff !important;
          box-shadow: 0 2px 8px rgba(38, 119, 255, 0.3);
          font-weight: 500;

          .el-icon {
            color: #fff !important;
          }
        }
      }
    }

    .sidebar-footer {
      padding: 14px 20px;
      border-top: 1px solid var(--tavily-border);

      .ai-status {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 12px;
        color: var(--tavily-text-muted);
        font-weight: 500;

        .status-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: #DC2626;
          box-shadow: 0 0 6px rgba(220, 38, 38, 0.3);
          transition: all 0.3s;
        }

        &.connected {
          color: var(--tavily-text-secondary);

          .status-dot {
            background: #16A34A;
            box-shadow: 0 0 6px rgba(22, 163, 74, 0.3);
          }
        }
      }
    }

    .logo-mini {
      height: 64px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-bottom: 1px solid var(--tavily-border);

      .el-icon {
        color: var(--tavily-text-primary);
      }
    }

    .collapse-btn {
      position: absolute;
      right: -12px;
      top: 50%;
      transform: translateY(-50%);
      width: 24px;
      height: 24px;
      border-radius: 50%;
      border: 1px solid var(--tavily-border);
      background: var(--tavily-bg-card);
      color: var(--tavily-text-secondary);
      backdrop-filter: blur(var(--glass-blur-sm));
      -webkit-backdrop-filter: blur(var(--glass-blur-sm));
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition:
        transform 0.15s cubic-bezier(0.16, 1, 0.3, 1),
        background-color 0.2s ease,
        color 0.2s ease,
        box-shadow 0.2s ease;
      z-index: 10;
      box-shadow: var(--tavily-shadow-sm);

      &:hover {
        background: var(--tavily-bg-card);
        color: var(--tavily-text-primary);
      }

      .el-icon {
        font-size: 14px;
        transition: transform 0.2s ease;

        &.rotated {
          transform: rotate(180deg);
        }
      }
    }

    &.collapsed {
      .sidebar-menu {
        :deep(.el-menu-item) {
          justify-content: center;

          span {
            display: none;
          }

          .el-icon {
            margin-right: 0;
          }
        }
      }
    }
  }

  .main-container {
    background: transparent;
    height: auto;

    .header {
      background: var(--tavily-bg-header);
      border-bottom: 1px solid var(--tavily-border);
      backdrop-filter: blur(var(--glass-blur-md));
      -webkit-backdrop-filter: blur(var(--glass-blur-md));
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 64px;
      padding: 0 16px;

      @media (min-width: 769px) {
        padding: 0 28px;
      }

      .header-left {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;

        h2 {
          font-size: 18px;
          font-weight: 500;
          color: var(--tavily-text-primary);
          letter-spacing: -0.01em;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          min-width: 0;

          @media (min-width: 769px) {
            font-size: 20px;
          }
        }

        .hamburger-btn {
          display: none;
          width: 36px;
          height: 36px;
          border-radius: var(--tavily-radius-md);
          border: 1px solid var(--tavily-border);
          background: var(--tavily-bg-card);
          color: var(--tavily-text-secondary);
          align-items: center;
          justify-content: center;
          cursor: pointer;
          transition: all 0.15s ease;

          @media (max-width: 768px) {
            display: flex;
          }

          &:hover {
            background: var(--tavily-primary-bg);
            color: var(--tavily-text-primary);
          }

          .el-icon {
            font-size: 18px;
          }
        }
      }

      .header-right {
        display: flex;
        gap: 10px;
        align-items: center;
        min-width: 0;

        .resource-bar {
          display: flex;
          flex-wrap: wrap;
          justify-content: flex-end;
          gap: 6px;
          font-size: 12px;
          font-weight: 500;

          .res-item {
            padding: 2px 6px;
            border-radius: var(--tavily-radius-md);
            background: var(--tavily-bg-card);
            border: 1px solid var(--tavily-border);
            color: var(--tavily-text-secondary);
            white-space: nowrap;

            &.warn { color: #D97706; border-color: #D97706; }
            &.danger { color: #DC2626; border-color: #DC2626; background: #FEF2F2; }
          }
        }

        @media (max-width: 768px) {
          display: none;
        }
      }
    }

    .main-content {
      padding: 16px;
      overflow-x: hidden;
      overflow-y: auto;
      background: transparent;

      @media (min-width: 769px) {
        padding: 20px 24px;
      }

      @media (min-width: 1024px) {
        padding: 24px 28px;
      }
    }
    }
}

.slide-fade-enter-active,
.slide-fade-leave-active {
  transition:
    opacity 0.3s var(--ease-smooth),
    transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}

.slide-fade-enter-from {
  opacity: 0;
  transform: translateY(18px) scale(0.985);
}

.slide-fade-leave-to {
  opacity: 0;
  transform: translateY(-12px) scale(0.985);
}
</style>
