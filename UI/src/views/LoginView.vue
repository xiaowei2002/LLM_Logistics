<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const username = ref('')
const password = ref('')

function handleSubmit() {
  if (authStore.login(username.value, password.value)) {
    password.value = ''
    router.replace(route.query.redirect || '/')
  }
}
</script>

<template>
  <div class="login-page">
    <form class="login-card" @submit.prevent="handleSubmit">
      <div class="brand">
        物流<span class="brand-accent">智能体</span><span class="brand-dot"></span>
      </div>
      <p class="subtitle">请登录后使用系统</p>

      <label class="field">
        <span class="field-label">账号</span>
        <input v-model="username" type="text" autocomplete="username" placeholder="请输入账号" />
      </label>

      <label class="field">
        <span class="field-label">密码</span>
        <input
          v-model="password"
          type="password"
          autocomplete="current-password"
          placeholder="请输入密码"
        />
      </label>

      <p v-if="authStore.error" class="error">{{ authStore.error }}</p>

      <button class="submit-btn" type="submit">登 录</button>
    </form>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
}

.login-card {
  width: 100%;
  max-width: 380px;
  border: 2px solid transparent;
  border-radius: var(--radius-lg);
  background:
    linear-gradient(var(--bg), var(--bg)) padding-box,
    linear-gradient(90deg, #ff8bd2, #b57bee, #5b8def) border-box;
  box-shadow: var(--shadow);
  padding: 36px 32px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.brand {
  font-size: 30px;
  font-weight: 800;
  letter-spacing: 1px;
  display: inline-flex;
  align-items: baseline;
  justify-content: center;
}

.brand-accent {
  color: var(--primary);
}

.brand-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: conic-gradient(#ff6b6b, #feca57, #48dbfb, #1dd1a1, #ff6b6b);
  margin-left: 6px;
  align-self: flex-end;
  margin-bottom: 6px;
}

.subtitle {
  color: var(--text-muted);
  text-align: center;
  margin-bottom: 8px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
}

.field input {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  font-size: 14px;
  font-family: inherit;
  color: var(--text);
  outline: none;
}

.field input::placeholder {
  color: var(--text-placeholder);
}

.field input:focus {
  border-color: var(--primary);
}

.error {
  color: #e54545;
  font-size: 13px;
}

.submit-btn {
  margin-top: 8px;
  border: none;
  border-radius: var(--radius-md);
  background: var(--primary);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 2px;
  padding: 11px 0;
  cursor: pointer;
}

.submit-btn:hover {
  opacity: 0.88;
}
</style>
