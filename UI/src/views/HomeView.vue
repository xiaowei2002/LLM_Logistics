<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, EditPen, Expand, RefreshRight } from '@element-plus/icons-vue'

import AppHero from '@/components/AppHero.vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
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
const draftText = ref('')
let abortController = null

const messages = computed(() => chatStore.current?.messages ?? [])

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

async function handleDeleteChat(id) {
  try {
    await ElMessageBox.confirm('确定删除这条对话吗？删除后无法恢复。', '删除对话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
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
    ElMessage.success('已复制')
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
    <ChatSidebar
      v-show="sidebarOpen"
      @new-chat="startNewChat"
      @select="handleSelectChat"
      @delete="handleDeleteChat"
      @logout="handleLogout"
      @collapse="sidebarOpen = false"
    />

    <!-- 右侧聊天区 -->
    <main class="main" :class="{ 'has-chat': messages.length, collapsed: !sidebarOpen }">
      <!-- 边栏收起后，左上角显示展开按钮 -->
      <el-button
        v-if="!sidebarOpen"
        class="expand-btn"
        circle
        @click="sidebarOpen = true"
      >
        <el-icon><Expand /></el-icon>
      </el-button>

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
                <el-tooltip content="复制" placement="top" :show-after="300">
                  <el-button class="tool-btn" text @click="copyMessage(msg)">
                    <el-icon><CopyDocument /></el-icon>
                  </el-button>
                </el-tooltip>
                <el-tooltip content="编辑" placement="top" :show-after="300">
                  <el-button class="tool-btn" text @click="handleEdit(index)">
                    <el-icon><EditPen /></el-icon>
                  </el-button>
                </el-tooltip>
              </div>

              <!-- AI 回复工具栏：复制 + 重新生成 -->
              <div v-if="msg.role === 'assistant' && !msg.streaming" class="msg-toolbar">
                <el-tooltip content="复制" placement="top" :show-after="300">
                  <el-button class="tool-btn" text @click="copyMessage(msg)">
                    <el-icon><CopyDocument /></el-icon>
                  </el-button>
                </el-tooltip>
                <el-tooltip content="重新生成" placement="top" :show-after="300">
                  <el-button class="tool-btn" text @click="handleRegenerate(index)">
                    <el-icon><RefreshRight /></el-icon>
                  </el-button>
                </el-tooltip>
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

/* 边栏收起后的展开按钮：悬在左上角 */
.expand-btn {
  position: fixed;
  top: 16px;
  left: 16px;
  z-index: 10;
  color: var(--text-muted);
  background: #fff;
  border: 1px solid var(--border);
}

.expand-btn:hover {
  color: var(--primary);
  border-color: var(--primary);
  background: #fff;
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
  color: var(--text-muted);
  padding: 4px 6px;
}

.tool-btn:hover {
  background: var(--fill-light);
  color: var(--text);
}

/* 用户消息的工具栏：图标按钮靠右 */
.user-toolbar {
  justify-content: flex-end;
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
