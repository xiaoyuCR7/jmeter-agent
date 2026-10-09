<template>
  <el-config-provider :locale="appStore.elementLocale">
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
import { useAppStore } from '@/store'

const appStore = useAppStore()
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html,
body,
#app {
  height: 100%;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
  background: var(--tavily-bg-gradient);
  color: var(--tavily-text-regular);
  font-size: 14px;
  line-height: 1.6;
}

/* Selection color — Tavily lavender accent */
::selection {
  background-color: rgba(129, 127, 255, 0.2);
  color: var(--tavily-text-primary);
}
</style>
