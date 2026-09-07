<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import AppHero from '@/components/AppHero.vue'
import TaskInput from '@/components/TaskInput.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

/** 已提交的任务内容，仅前端展示 */
const submittedTask = ref('')

function handleSend(text) {
  // TODO: 后端 Agent 接入后替换为真实结果展示
  submittedTask.value = text
}

function handleLogout() {
  authStore.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="page">
    <div class="topbar">
      <span class="user">{{ authStore.displayName }}</span>
      <button class="logout-btn" type="button" @click="handleLogout">退出登录</button>
    </div>

    <main class="main">
      <AppHero />

      <TaskInput @send="handleSend" />

      <section v-if="submittedTask" class="preview">
        <p class="preview-label">已提交任务</p>
        <p class="preview-text">{{ submittedTask }}</p>
      </section>
    </main>
  </div>
</template>

<style scoped>
.page {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px 32px 48px;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}

.user {
  color: var(--text-muted);
  font-size: 13px;
}

.logout-btn {
  border: 1px solid var(--border);
  background: #fff;
  border-radius: var(--radius-md);
  padding: 6px 14px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
}

.logout-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.preview {
  max-width: 760px;
  width: 100%;
  margin: 24px auto 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px 18px;
  background: var(--fill-light);
}

.preview-label {
  color: var(--text-muted);
  font-size: 13px;
  margin-bottom: 6px;
}

.preview-text {
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
