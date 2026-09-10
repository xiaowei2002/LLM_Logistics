<script setup>
import { computed, ref } from 'vue'
import { Delete, Fold, Plus, Search, Share, SwitchButton, Tools, TrendCharts } from '@element-plus/icons-vue'

import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'

const emit = defineEmits(['new-chat', 'select', 'delete', 'logout', 'collapse', 'open-settings', 'open-graph', 'open-forecast'])

const authStore = useAuthStore()
const chatStore = useChatStore()

const searchOpen = ref(false)
const searchText = ref('')

/* 历史会话按时间分组：今天 / 7 天内 / 30 天内 / 更早（支持标题搜索过滤） */
const groupedConversations = computed(() => {
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = 24 * 60 * 60 * 1000
  const q = searchText.value.trim().toLowerCase()
  const buckets = { today: [], week: [], month: [], older: [] }
  for (const c of [...chatStore.conversations]
    .filter((c) => !q || c.title.toLowerCase().includes(q))
    .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))) {
    const t = c.updatedAt ?? 0
    if (t >= startOfToday) buckets.today.push(c)
    else if (t >= startOfToday - 7 * day) buckets.week.push(c)
    else if (t >= startOfToday - 30 * day) buckets.month.push(c)
    else buckets.older.push(c)
  }
  return [
    { label: '今天', items: buckets.today },
    { label: '7 天内', items: buckets.week },
    { label: '30 天内', items: buckets.month },
    { label: '更早', items: buckets.older },
  ].filter((g) => g.items.length)
})

const hasResult = computed(() => groupedConversations.value.length > 0)

function toggleSearch() {
  searchOpen.value = !searchOpen.value
  searchText.value = ''
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-head">
      <div class="sidebar-logo">物流<span class="accent">智能体</span><span class="dot"></span></div>
      <div class="head-actions">
        <el-tooltip content="搜索对话" placement="bottom" :show-after="300">
          <el-button class="icon-btn" circle text @click="toggleSearch">
            <el-icon><Search /></el-icon>
          </el-button>
        </el-tooltip>
        <el-tooltip content="收起边栏" placement="bottom" :show-after="300">
          <el-button class="icon-btn" circle text @click="emit('collapse')">
            <el-icon><Fold /></el-icon>
          </el-button>
        </el-tooltip>
      </div>
    </div>

    <el-input
      v-if="searchOpen"
      v-model="searchText"
      class="search-input"
      placeholder="搜索对话…"
      clearable
      :prefix-icon="Search"
    />

    <el-button class="new-chat-btn" type="primary" plain @click="emit('new-chat')">
      <el-icon class="plus-icon"><Plus /></el-icon>
      <span>开启新对话</span>
    </el-button>

    <el-scrollbar class="history">
      <template v-for="group in groupedConversations" :key="group.label">
        <div class="group-label">{{ group.label }}</div>
        <div
          v-for="c in group.items"
          :key="c.id"
          class="history-item"
          :class="{ active: c.id === chatStore.currentId }"
          @click="emit('select', c.id)"
        >
          <span class="history-title">{{ c.title }}</span>
          <el-tooltip content="删除对话" placement="top" :show-after="300">
            <el-button
              class="history-del"
              text
              circle
              @click.stop="emit('delete', c.id)"
            >
              <el-icon><Delete /></el-icon>
            </el-button>
          </el-tooltip>
        </div>
      </template>

      <el-empty
        v-if="!hasResult"
        class="history-empty"
        description="暂无对话记录"
        :image-size="64"
      />
    </el-scrollbar>

    <div class="sidebar-footer">
      <el-dropdown
        trigger="click"
        @command="
          (cmd) =>
            emit(
              cmd === 'settings' ? 'open-settings' : cmd === 'graph' ? 'open-graph' : cmd === 'forecast' ? 'open-forecast' : cmd,
            )
        "
      >
        <span class="user-trigger">
          <el-avatar :size="28" class="user-avatar">{{ authStore.displayName.slice(0, 1) }}</el-avatar>
          <span class="user-name">{{ authStore.displayName }}</span>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="forecast" :icon="TrendCharts">需求预测</el-dropdown-item>
            <el-dropdown-item command="graph" :icon="Share">知识图谱</el-dropdown-item>
            <el-dropdown-item command="settings" :icon="Tools">系统设置</el-dropdown-item>
            <el-dropdown-item command="logout" :icon="SwitchButton" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 240px;
  flex-shrink: 0;
  height: 100%;
  background: #e8eaf0;
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
}

/* ===== 头部 ===== */
.sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.sidebar-logo {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 1px;
  display: inline-flex;
  align-items: baseline;
  color: var(--text);
}

.sidebar-logo .accent {
  color: var(--primary);
}

.sidebar-logo .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: conic-gradient(#ff6b6b, #feca57, #48dbfb, #1dd1a1, #ff6b6b);
  margin-left: 5px;
  align-self: flex-end;
  margin-bottom: 4px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.icon-btn {
  color: var(--text-muted);
}

.icon-btn :deep(.el-icon) {
  font-size: 18px;
}

.icon-btn:hover {
  color: var(--text);
  background: #dde0e8;
}

/* ===== 搜索 ===== */
.search-input {
  margin-bottom: 10px;
}

.search-input :deep(.el-input__wrapper) {
  border-radius: 999px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.search-input :deep(.el-input__prefix),
.search-input :deep(.el-input__suffix) {
  font-size: 16px;
}

/* ===== 新对话按钮 ===== */
.new-chat-btn {
  width: 100%;
  border-radius: 999px;
  justify-content: flex-start;
}

.plus-icon {
  margin-right: 4px;
  font-size: 16px;
}

/* ===== 历史列表 ===== */
.history {
  flex: 1;
  min-height: 0;
  margin-top: 8px;
}

.group-label {
  margin: 14px 8px 6px;
  font-size: 12px;
  color: var(--text-muted);
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 12px;
  border-radius: 8px;
  font-size: 13.5px;
  color: var(--text);
  cursor: pointer;
  transition: background 0.15s;
}

.history-item:hover {
  background: #dde0e8;
}

.history-item.active {
  background: var(--primary-light);
  color: var(--primary);
  font-weight: 600;
}

.history-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 删除按钮：hover 时才出现 */
.history-del {
  flex-shrink: 0;
  color: var(--text-muted);
  opacity: 0;
  transition: opacity 0.15s;
}

.history-del :deep(.el-icon) {
  font-size: 16px;
}

.history-item:hover .history-del {
  opacity: 1;
}

.history-del:hover {
  color: #e5484d;
}

.history-empty {
  margin-top: 40px;
}

.history-empty :deep(.el-empty__description p) {
  color: var(--text-muted);
  font-size: 12px;
}

/* ===== 底部用户区 ===== */
.sidebar-footer {
  display: flex;
  align-items: center;
  padding-top: 12px;
  margin-top: 8px;
  border-top: 1px solid #dde0e8;
}

.user-trigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 13px;
  outline: none;
}

.user-trigger:hover {
  background: #dde0e8;
  color: var(--text);
}

.user-avatar {
  background: var(--primary);
  color: #fff;
  font-size: 13px;
}

.user-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
