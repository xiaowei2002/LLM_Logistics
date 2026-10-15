<script setup>
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Collection, SwitchButton, Tools } from '@element-plus/icons-vue'

import SettingsDialog from '@/components/SettingsDialog.vue'
import { decisionModules } from '@/config/modules'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const settingsOpen = ref(false)

function handleCommand(command) {
  if (command === 'settings') {
    settingsOpen.value = true
  } else if (command === 'logout') {
    authStore.logout()
    router.replace({ name: 'login' })
  }
}

function notifySoon(mod) {
  ElMessage.info(`${mod.title}模块建设中，敬请期待`)
}
</script>

<template>
  <!-- 各功能页共用的系统顶栏：品牌回首页 + 模块切换 + 页面操作（actions 插槽）+ 用户菜单 -->
  <header class="app-nav">
    <RouterLink to="/" class="nav-brand" title="系统首页">
      <span class="brand-mark" aria-hidden="true"><i></i></span>
      <span class="brand-text">智慧物流决策系统</span>
    </RouterLink>

    <nav class="nav-tabs" aria-label="决策模块">
      <template v-for="mod in decisionModules" :key="mod.key">
        <RouterLink v-if="mod.to" :to="mod.to" class="nav-tab" active-class="active">
          <el-icon><component :is="mod.icon" /></el-icon>
          <span>{{ mod.title }}</span>
        </RouterLink>
        <button v-else type="button" class="nav-tab soon" @click="notifySoon(mod)">
          <el-icon><component :is="mod.icon" /></el-icon>
          <span>{{ mod.title }}</span>
          <small>建设中</small>
        </button>
      </template>
    </nav>

    <div class="nav-right">
      <div class="nav-actions"><slot name="actions" /></div>
      <RouterLink to="/knowledge" class="nav-kb" active-class="active">
        <el-icon><Collection /></el-icon><span>知识库</span>
      </RouterLink>
      <el-dropdown trigger="click" @command="handleCommand">
        <button type="button" class="nav-user" :aria-label="`用户菜单：${authStore.displayName}`">
          <el-avatar :size="28" class="nav-avatar">{{ authStore.displayName.slice(0, 1) }}</el-avatar>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="settings" :icon="Tools">系统设置</el-dropdown-item>
            <el-dropdown-item command="logout" :icon="SwitchButton" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
  <SettingsDialog v-model:open="settingsOpen" />
</template>

<style scoped>
.app-nav {
  position: relative;
  z-index: 20;
  flex: none;
  display: flex;
  align-items: center;
  gap: 24px;
  height: 56px;
  padding: 0 20px;
  background: linear-gradient(90deg, #001c2d, var(--primary) 45%, #00324d);
  color: #fff;
  box-shadow: 0 1px 0 #ffffff14, 0 6px 18px rgba(0, 27, 44, 0.18);
}

/* 底边一道缓慢流动的光，呼应首页的数据流动效 */
.app-nav::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, #2ce0b4 50%, transparent) no-repeat;
  background-size: 30% 100%;
  animation: nav-flow 7s linear infinite;
  opacity: 0.7;
}

@keyframes nav-flow {
  from { background-position: -40% 0; }
  to { background-position: 140% 0; }
}

/* ===== 品牌 ===== */
.nav-brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  color: #fff;
  text-decoration: none;
  border-radius: 6px;
}

.nav-brand:focus-visible {
  outline: 2px solid #2ce0b4;
  outline-offset: 4px;
}

.brand-mark {
  position: relative;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 1.5px solid #2ce0b4;
  border-radius: 6px;
  transform: rotate(45deg);
  transition: transform 0.5s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.brand-mark i {
  width: 7px;
  height: 7px;
  border-radius: 2px;
  background: #2ce0b4;
  box-shadow: 0 0 10px #2ce0b4;
  animation: mark-pulse 2.4s ease-in-out infinite;
}

.nav-brand:hover .brand-mark {
  transform: rotate(225deg);
}

@keyframes mark-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.55; transform: scale(0.7); }
}

.brand-text {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 2px;
  white-space: nowrap;
}

/* ===== 模块切换 ===== */
.nav-tabs {
  display: flex;
  align-items: stretch;
  align-self: stretch;
  gap: 4px;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.nav-tabs::-webkit-scrollbar {
  display: none;
}

.nav-tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex-shrink: 0;
  padding: 0 14px;
  border: 0;
  background: transparent;
  color: #a9bccb;
  font: inherit;
  font-size: 14px;
  white-space: nowrap;
  text-decoration: none;
  cursor: pointer;
  transition: color 0.2s, background 0.2s;
}

.nav-tab .el-icon {
  font-size: 16px;
  transition: transform 0.25s;
}

.nav-tab::after {
  content: '';
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 0;
  height: 3px;
  border-radius: 3px 3px 0 0;
  background: #2ce0b4;
  box-shadow: 0 0 12px #2ce0b4;
  transform: scaleX(0);
  transition: transform 0.3s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.nav-tab:hover {
  color: #fff;
  background: #ffffff0d;
}

.nav-tab:hover .el-icon {
  transform: translateY(-2px);
}

.nav-tab:hover::after {
  transform: scaleX(0.4);
}

.nav-tab.active {
  color: #fff;
  font-weight: 600;
}

.nav-tab.active .el-icon {
  color: #2ce0b4;
}

.nav-tab.active::after {
  transform: scaleX(1);
}

.nav-tab:focus-visible {
  outline: 2px solid #2ce0b4;
  outline-offset: -4px;
}

.nav-tab.soon {
  color: #6f8798;
}

.nav-tab.soon:hover::after {
  transform: scaleX(0);
}

.nav-tab small {
  padding: 1px 6px;
  border-radius: 999px;
  background: #f5b54a26;
  color: #f5c46e;
  font-size: 11px;
}

/* ===== 右侧：页面操作 + 用户 ===== */
.nav-right {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
  flex-shrink: 0;
}

.nav-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  /* Element Plus 文字按钮在深色顶栏上的配色 */
  --el-text-color-regular: #d6e1e8;
  --el-fill-color-light: #ffffff14;
  --el-fill-color: #ffffff26;
  --el-fill-color-dark: #ffffff33;
  --el-disabled-text-color: #ffffff4d;
}

.nav-actions:empty {
  display: none;
}

.nav-actions :deep(.el-button.el-button) {
  margin-left: 0;
  border: 0;
  border-radius: 6px;
}

.nav-actions :deep(.el-button.el-button:not(.is-disabled):hover) {
  color: #fff;
}

/* 知识库入口：与模块切换区分开，放在右侧工具区 */
.nav-kb {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid #ffffff26;
  border-radius: 8px;
  color: #d6e1e8;
  font-size: 13.5px;
  text-decoration: none;
  white-space: nowrap;
  transition: color 0.2s, background 0.2s, border-color 0.2s;
}

.nav-kb:hover,
.nav-kb:focus-visible {
  outline: none;
  color: #fff;
  background: #ffffff14;
}

.nav-kb.active {
  border-color: #2ce0b4;
  color: #2ce0b4;
  background: #2ce0b414;
}

.nav-user {
  display: inline-flex;
  padding: 2px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  transition: box-shadow 0.2s;
}

.nav-user:hover,
.nav-user:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px #2ce0b4;
}

.nav-avatar {
  background: #2ce0b4;
  color: var(--primary);
  font-weight: 700;
}

@media (max-width: 1080px) {
  .brand-text {
    display: none;
  }
}

@media (max-width: 640px) {
  .app-nav {
    gap: 8px;
    padding: 0 12px;
  }

  .nav-tab {
    padding: 0 10px;
  }

  .nav-tab small,
  .nav-kb span {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .app-nav::after,
  .brand-mark i {
    animation: none;
  }
}
</style>
