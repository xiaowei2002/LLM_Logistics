<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import AppHero from '@/components/AppHero.vue'
import TaskInput from '@/components/TaskInput.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { streamChat } from '@/services/llm'

const router = useRouter()
const authStore = useAuthStore()
const chatStore = useChatStore()

const chatListRef = ref(null)
const deepThink = ref(true)
const sidebarOpen = ref(true)
const searchOpen = ref(false)
const searchText = ref('')
const draftText = ref('')
let abortController = null

const messages = computed(() => chatStore.current?.messages ?? [])

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

onMounted(() => {
  if (!chatStore.current) chatStore.newChat()
})

// 切换对话后滚动到底部
watch(() => chatStore.currentId, () => scrollToBottom())

async function scrollToBottom() {
  await nextTick()
  const el = chatListRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function startNewChat() {
  abortController?.abort()
  chatStore.newChat()
}

function handleSelectChat(id) {
  if (id === chatStore.currentId) return
  abortController?.abort()
  chatStore.selectChat(id)
}

function handleDeleteChat(id) {
  if (!window.confirm('确定删除这条对话吗？删除后无法恢复。')) return
  if (id === chatStore.currentId) abortController?.abort()
  chatStore.deleteChat(id)
  if (!chatStore.current) chatStore.newChat()
}

async function handleSend(text) {
  const conv = chatStore.current
  if (!conv || conv.messages.some((m) => m.streaming)) return

  conv.messages.push({ role: 'user', content: text })
  // 用第一条提问作为对话标题
  if (conv.title === '新对话') {
    conv.title = text.length > 20 ? text.slice(0, 20) + '…' : text
  }
  generateReply(conv)
}

/* 生成一条 AI 回复（流式） */
async function generateReply(conv) {
  abortController?.abort()
  abortController = new AbortController()

  conv.messages.push({
    role: 'assistant',
    content: '',
    reasoning: '',
    reasoningOpen: true,
    thinkSeconds: null,
    streaming: true,
  })
  // 取 push 后的响应式代理对象，直接改原始对象不会触发页面更新
  const reply = conv.messages[conv.messages.length - 1]
  const thinkStart = Date.now()
  scrollToBottom()

  try {
    // 只把已完成的对话传给模型
    const history = conv.messages
      .filter((m) => !m.streaming)
      .map((m) => ({ role: m.role, content: m.content }))
    await streamChat(
      history,
      ({ content, reasoning }) => {
        if (reasoning) reply.reasoning += reasoning
        if (content) {
          // 第一段正文到达时记录思考耗时并自动收起思考过程
          if (!reply.content && reply.reasoning) {
            reply.thinkSeconds = Math.max(1, Math.round((Date.now() - thinkStart) / 1000))
            reply.reasoningOpen = false
          }
          reply.content += content
        }
        scrollToBottom()
      },
      abortController.signal,
      { deepThink: deepThink.value },
    )
  } catch (err) {
    if (err.name !== 'AbortError') {
      reply.content += reply.content ? '\n\n' : ''
      reply.content += `[请求出错：${err.message}]`
    }
  } finally {
    reply.streaming = false
    conv.updatedAt = Date.now()
    chatStore.persist()
    scrollToBottom()
  }
}

/* 重新生成：删掉这条回复及之后内容，基于已有上下文重新流式生成 */
function handleRegenerate(msgIndex) {
  const conv = chatStore.current
  if (!conv || conv.messages.some((m) => m.streaming)) return
  abortController?.abort()
  conv.messages.splice(msgIndex)
  generateReply(conv)
}

/* 复制 AI 回复内容 */
async function copyMessage(msg) {
  try {
    await navigator.clipboard.writeText(msg.content)
    msg.copied = true
    setTimeout(() => (msg.copied = false), 1500)
  } catch {
    // 剪贴板不可用时静默失败
  }
}

/* 编辑用户消息：撤回该条及之后的对话，内容放回输入框 */
function handleEdit(msgIndex) {
  const conv = chatStore.current
  if (!conv || conv.messages.some((m) => m.streaming)) return
  const text = conv.messages[msgIndex]?.content
  if (text == null) return
  abortController?.abort()
  conv.messages.splice(msgIndex)
  // 先清空再赋值，保证连续编辑同一条时 watch 也能触发
  draftText.value = ''
  nextTick(() => {
    draftText.value = text
  })
}

function handleLogout() {
  abortController?.abort()
  authStore.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="page">
    <!-- 左侧边栏：新对话 + 历史记录 -->
    <aside v-show="sidebarOpen" class="sidebar">
      <div class="sidebar-head">
        <div class="sidebar-logo">物流<span class="accent">智能体</span><span class="dot"></span></div>
        <div class="head-actions">
          <button
            class="icon-btn"
            type="button"
            title="搜索对话"
            @click="searchOpen = !searchOpen; searchText = ''"
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
          </button>
          <button
            class="icon-btn"
            type="button"
            title="收起边栏"
            @click="sidebarOpen = false"
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="9" y1="4" x2="9" y2="20"/></svg>
          </button>
        </div>
      </div>

      <input
        v-if="searchOpen"
        v-model="searchText"
        class="search-input"
        type="text"
        placeholder="搜索对话…"
        autofocus
      />

      <button class="new-chat-btn" type="button" @click="startNewChat">
        <span class="plus">+</span> 开启新对话
      </button>

      <div class="history">
        <template v-for="group in groupedConversations" :key="group.label">
          <div class="group-label">{{ group.label }}</div>
          <div
            v-for="c in group.items"
            :key="c.id"
            class="history-item"
            :class="{ active: c.id === chatStore.currentId }"
            @click="handleSelectChat(c.id)"
          >
            <span class="history-title">{{ c.title }}</span>
            <button
              class="history-del"
              type="button"
              title="删除对话"
              @click.stop="handleDeleteChat(c.id)"
            >
              ✕
            </button>
          </div>
        </template>
      </div>

      <div class="sidebar-footer">
        <span class="user">{{ authStore.displayName }}</span>
        <button class="logout-btn" type="button" @click="handleLogout">退出登录</button>
      </div>
    </aside>

    <!-- 右侧聊天区 -->
    <main class="main" :class="{ 'has-chat': messages.length, collapsed: !sidebarOpen }">
      <!-- 边栏收起后，左上角显示展开按钮 -->
      <button
        v-if="!sidebarOpen"
        class="expand-btn"
        type="button"
        title="展开边栏"
        @click="sidebarOpen = true"
      >
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="9" y1="4" x2="9" y2="20"/></svg>
      </button>
      <!-- 未开始对话：居中展示 Logo 和输入框 -->
      <template v-if="!messages.length">
        <AppHero />
        <TaskInput
          :deep-think="deepThink"
          :draft="draftText"
          @toggle-deep="deepThink = !deepThink"
          @send="handleSend"
        />
      </template>

      <!-- 对话中：聊天记录在上方滚动，输入框固定底部 -->
      <template v-else>
        <!-- 内容区头部：当前会话标题 -->
        <div class="chat-head">
          <h1 class="chat-title">{{ chatStore.current?.title }}</h1>
        </div>

        <section class="chat-scroll" ref="chatListRef">
            <div class="chat-inner">
              <div
                v-for="(msg, index) in messages"
                :key="index"
                class="chat-item"
                :class="msg.role"
              >
                <div class="bubble">
                  <!-- 深度思考过程：可折叠 -->
                  <div v-if="msg.reasoning" class="reasoning-box">
                    <button
                      class="reasoning-toggle"
                      type="button"
                      @click="msg.reasoningOpen = !msg.reasoningOpen"
                    >
                      <template v-if="msg.streaming && !msg.content">思考中…</template>
                      <template v-else>
                        已深度思考<template v-if="msg.thinkSeconds">（用时 {{ msg.thinkSeconds }} 秒）</template>
                      </template>
                      <span class="chevron" :class="{ open: msg.reasoningOpen }">▾</span>
                    </button>
                    <div v-show="msg.reasoningOpen" class="reasoning-body">{{ msg.reasoning }}</div>
                  </div>

                  <span v-if="!msg.content && !(msg.role === 'assistant' && msg.reasoning)" class="dots">
                    <i></i><i></i><i></i>
                  </span>
                  <span class="text">{{ msg.content }}</span>
                  <span v-if="msg.streaming && msg.content" class="caret"></span>
                </div>

                <!-- 用户消息工具栏：复制 + 编辑 -->
                <div v-if="msg.role === 'user' && !msg.streaming" class="msg-toolbar user-toolbar">
                  <button class="tool-btn" type="button" data-tip="复制" @click="copyMessage(msg)">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
                  </button>
                  <button class="tool-btn" type="button" data-tip="编辑" @click="handleEdit(index)">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
                  </button>
                </div>

                <!-- AI 回复工具栏：复制 + 重新生成，图标 + 悬浮提示 -->
                <div v-if="msg.role === 'assistant' && !msg.streaming" class="msg-toolbar">
                  <button class="tool-btn" type="button" :data-tip="msg.copied ? '已复制' : '复制'" @click="copyMessage(msg)">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
                  </button>
                  <button class="tool-btn" type="button" data-tip="重新生成" @click="handleRegenerate(index)">
                    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><line x1="21" y1="3" x2="21" y2="9"/><line x1="15" y1="9" x2="21" y2="9"/></svg>
                  </button>
                </div>
              </div>
            </div>
        </section>

        <div class="input-dock">
          <TaskInput
            :deep-think="deepThink"
            :draft="draftText"
            compact
            @toggle-deep="deepThink = !deepThink"
            @send="handleSend"
          />
        </div>
      </template>
    </main>
  </div>
</template>

<style scoped>
.page {
  height: 100vh;
  display: flex;
  overflow: hidden;
}

/* ===== 侧边栏：中浅灰底、无边框，和白色主区保持可辨识的色差 ===== */
.sidebar {
  width: 240px;
  flex-shrink: 0;
  background: #e8eaf0;
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
}

/* 侧边栏头部：Logo + 图标按钮 */
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
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  color: var(--text-muted);
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.icon-btn:hover {
  background: #dde0e8;
  color: var(--text);
}

/* 搜索输入框 */
.search-input {
  width: 100%;
  border: none;
  outline: none;
  border-radius: 999px;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  padding: 8px 14px;
  font-size: 13px;
  color: var(--text);
  margin-bottom: 10px;
}

.search-input::placeholder {
  color: #c3c9d2;
}

/* 边栏收起后的展开按钮：悬在左上角 */
.expand-btn {
  position: fixed;
  top: 16px;
  left: 16px;
  z-index: 10;
  width: 32px;
  height: 32px;
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text-muted);
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.expand-btn:hover {
  color: var(--primary);
  border-color: var(--primary);
}

/* 新对话按钮：胶囊形白色底，靠色差和阴影区分，不画边框 */
.new-chat-btn {
  width: 100%;
  padding: 11px 16px;
  border: none;
  border-radius: 999px;
  background: #ffffff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  font-size: 14px;
  color: var(--text);
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
}

.new-chat-btn:hover {
  background: var(--primary-light);
  color: var(--primary);
}

.new-chat-btn .plus {
  margin-right: 4px;
  font-weight: 600;
}

.history {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 8px;
  scrollbar-width: thin;
  scrollbar-color: #d3d7de transparent;
}

.history::-webkit-scrollbar {
  width: 6px;
}

.history::-webkit-scrollbar-thumb {
  background: #d3d7de;
  border-radius: 3px;
}

/* 分组标题：纯文本，无边框无盒子，靠间距分组 */
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

/* 选中态：整行填充浅蓝底，不加边框 */
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
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0;
  transition: all 0.15s;
}

.history-item:hover .history-del {
  opacity: 1;
}

.history-del:hover {
  color: #e5484d;
  background: rgba(229, 72, 77, 0.1);
}

.sidebar-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 12px;
  margin-top: 8px;
}

.user {
  color: var(--text-muted);
  font-size: 13px;
}

.logout-btn {
  border: none;
  background: none;
  padding: 4px 8px;
  font-size: 12.5px;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: 6px;
}

.logout-btn:hover {
  color: var(--primary);
  background: var(--primary-light);
}

/* ===== 右侧聊天区 ===== */
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 20px 32px 24px;
}

.main.has-chat {
  justify-content: flex-start;
}

/* 边栏收起后：内容左移出展开按钮的占位，避免被遮住 */
.main.collapsed {
  padding-left: 64px;
}

/* 内容区头部：会话标题，纯文本无框，靠侧栏一侧左对齐 */
.chat-head {
  width: 100%;
  margin: 0;
  padding: 4px 4px 0;
}

.chat-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
  text-align: left;
  margin: 0 0 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 消息滚动区：占满主区宽度，滚动条贴页面右缘（内容列另行居中） */
.chat-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  width: 100%;
  padding: 24px 16px 8px;
  display: flex;
  flex-direction: column;
  scrollbar-width: thin;
  scrollbar-color: #d3d7de transparent;
}

/* 细滚动条：浅灰、圆角，只有溢出时才出现 */
.chat-scroll::-webkit-scrollbar {
  width: 6px;
}

.chat-scroll::-webkit-scrollbar-thumb {
  background: #d3d7de;
  border-radius: 3px;
}

.chat-scroll::-webkit-scrollbar-track {
  background: transparent;
}

/* 消息列表：760px 内容列居中，永远从顶部开始排 */
.chat-inner {
  margin: 0 auto;
  max-width: 760px;
  width: 100%;
  display: flex;
  flex-direction: column;
}

/* 组内（问↔答）间距 16px；组间（上一答↔下一问）拉开到 24px */
.chat-item + .chat-item {
  margin-top: 16px;
}

.chat-item.user:not(:first-child) {
  margin-top: 24px;
}

.chat-item {
  display: flex;
}

/* 用户消息：靠右、淡蓝胶囊、无边框，右侧预留 padding */
.chat-item.user {
  flex-direction: column;
  align-items: flex-end;
  padding-right: 10px;
}

.chat-item.assistant {
  /* AI 消息：整块卡片，竖排容纳正文 + 工具栏 */
  flex-direction: column;
  align-items: stretch;
}

.bubble {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-item.user .bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 20px;
  background: var(--primary-light);
  color: var(--text);
}

/* AI 消息：无气泡底色，整块平铺，与标题同左缘 */
.chat-item.assistant .bubble {
  width: 100%;
  padding: 4px 2px;
  color: var(--text);
}

/* AI 回复工具栏：hover 显示 */
.msg-toolbar {
  display: flex;
  gap: 4px;
  margin-top: 6px;
  opacity: 0;
  transition: opacity 0.15s;
}

.chat-item:hover .msg-toolbar {
  opacity: 1;
}

.tool-btn {
  position: relative;
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 12px;
  padding: 4px 6px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.tool-btn:hover {
  background: var(--fill-light);
  color: var(--text);
}

/* 悬浮功能提示：小气泡 */
.tool-btn::after {
  content: attr(data-tip);
  position: absolute;
  top: calc(100% + 5px);
  left: 50%;
  transform: translateX(-50%);
  background: #1f2329;
  color: #fff;
  font-size: 12px;
  line-height: 1;
  padding: 5px 8px;
  border-radius: 6px;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s;
  z-index: 5;
}

.tool-btn:hover::after {
  opacity: 1;
}

/* 用户消息的工具栏：图标按钮靠右 */
.user-toolbar {
  justify-content: flex-end;
}

.user-toolbar .tool-btn {
  padding: 4px 6px;
}

/* 深度思考过程：可折叠 */
.reasoning-box {
  margin-bottom: 10px;
}

.reasoning-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: none;
  padding: 0;
  font-size: 13px;
  color: var(--text-muted);
  cursor: pointer;
}

.reasoning-toggle:hover {
  color: var(--text);
}

.chevron {
  display: inline-block;
  transition: transform 0.15s;
  font-size: 11px;
}

.chevron.open {
  transform: rotate(180deg);
}

.reasoning-body {
  margin-top: 8px;
  padding: 10px 12px;
  background: var(--fill-light);
  border-left: 3px solid #d3d7de;
  border-radius: 6px;
  font-size: 12.5px;
  color: var(--text-muted);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow-y: auto;
  scrollbar-width: thin;
}

/* "思考中"的三个跳动圆点 */
.dots {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  height: 18px;
}

.dots i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-muted);
  animation: bounce 1.2s infinite ease-in-out;
}

.dots i:nth-child(2) {
  animation-delay: 0.15s;
}

.dots i:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes bounce {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
}

/* 流式生成中的光标 */
.caret {
  display: inline-block;
  width: 2px;
  height: 14px;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: var(--primary);
  animation: blink 0.8s infinite step-end;
}

@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0;
  }
}

/* 输入框固定在底边，宽度与消息列对齐 */
.input-dock {
  max-width: 760px;
  width: 100%;
  margin: 0 auto;
  padding-bottom: 12px;
}
</style>
