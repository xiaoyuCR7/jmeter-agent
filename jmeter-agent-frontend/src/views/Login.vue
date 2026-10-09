<template>
  <main class="login-page">
    <el-card class="login-card">
      <h1>JMeter Agent</h1>
      <p>请使用管理员配置的账号登录 / Sign in</p>
      <el-form @submit.prevent="signIn" label-position="top">
        <el-form-item label="用户名 / Username">
          <el-input v-model="username" autocomplete="username" autofocus />
        </el-form-item>
        <el-form-item label="密码 / Password">
          <el-input v-model="password" type="password" autocomplete="current-password" show-password />
        </el-form-item>
        <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
        <el-button type="primary" native-type="submit" :loading="loading" :disabled="!username || !password">登录 / Sign in</el-button>
      </el-form>
    </el-card>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'

const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')
const route = useRoute()
const router = useRouter()
async function signIn() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    await api.login(username.value, password.value)
    password.value = ''
    const destination = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(destination.startsWith('/') && !destination.startsWith('//') ? destination : '/')
  } catch (err: any) {
    error.value = err.response?.status === 401 ? '用户名或密码错误 / Invalid credentials' : '登录失败，请检查服务连接 / Sign in failed'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
.login-card { width: min(100%, 420px); }
h1 { font-size: 24px; }
p { margin: 12px 0 24px; }
.el-button { margin-top: 20px; width: 100%; }
</style>
