<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const formRef = ref(null)
const form = reactive({
  username: '',
  password: '',
})

function handleSubmit() {
  if (authStore.login(form.username, form.password)) {
    form.password = ''
    router.replace(route.query.redirect || '/')
  } else if (authStore.error) {
    ElMessage.error(authStore.error)
  }
}
</script>

<template>
  <div class="login-page">
    <el-form
      ref="formRef"
      class="login-card"
      :model="form"
      label-position="top"
      @submit.prevent="handleSubmit"
    >
      <div class="brand">
        物流<span class="brand-accent">智能体</span><span class="brand-dot"></span>
      </div>
      <p class="subtitle">请登录后使用系统</p>

      <el-form-item label="账号">
        <el-input
          v-model="form.username"
          placeholder="请输入账号"
          autocomplete="username"
          :prefix-icon="User"
          clearable
        />
      </el-form-item>

      <el-form-item label="密码">
        <el-input
          v-model="form.password"
          type="password"
          placeholder="请输入密码"
          autocomplete="current-password"
          :prefix-icon="Lock"
          show-password
        />
      </el-form-item>

      <el-button class="submit-btn" type="primary" native-type="submit">登 录</el-button>
    </el-form>
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
  gap: 8px;
}

.brand {
  font-size: 30px;
  font-weight: 800;
  letter-spacing: 1px;
  display: inline-flex;
  align-items: baseline;
  justify-content: center;
  margin-bottom: 4px;
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

.submit-btn {
  margin-top: 12px;
  letter-spacing: 2px;
  font-weight: 600;
  font-size: 15px;
}
</style>
