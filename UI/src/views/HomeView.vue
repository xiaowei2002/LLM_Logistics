<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, EditPen, Expand, Paperclip, RefreshRight } from '@element-plus/icons-vue'

import AppHero from '@/components/AppHero.vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import SettingsDialog from '@/components/SettingsDialog.vue'
import TaskInput from '@/components/TaskInput.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { renderMarkdown } from '@/utils/markdown'
import { streamChat } from '@/services/llm'

const router = useRouter()
const authStore = useAuthStore()
const chatStore = useChatStore()

const chatListRef = ref(null)
const deepThink = ref(true)
const sidebarOpen = ref(true)
const settingsOpen = ref(false)
const draftText = ref('')
let abortController = null

/* 两个输入框实例（空状态 / 聊天态）：用于重新附加历史文档 */
const heroInputRef = ref(null)
const chatInputRef = ref(null)

const messages = computed(() => chatStore.current?.messages ?? [])

/* 是否有回复正在生成（发送键变停止键） */
const streaming = computed(() => chatStore.current?.messages.some((m) => m.streaming))

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

async function handleSend(text, images = [], docs = []) {
  const conv = chatStore.current
  if (!conv || conv.messages.some((m) => m.streaming)) return

  // 文档随消息保存（名字用于展示，文字发给模型时再拼接）
  conv.messages.push({ role: 'user', content: text, images, docs })
  // 用第一条提问作为对话标题
  if (conv.title === '新对话') {
    conv.title = text
      ? text.length > 20 ? text.slice(0, 20) + '…' : text
      : images.length ? '[图片]' : '[文档]'
  }
  generateReply(conv)
}

/* 消息转 API 格式：带图的用户消息 content 为数组（OpenAI 视觉格式） */
function toApiMessage(m) {
  // 文档提取的文字并入消息文本，模型才能读到文档内容
  let text = m.content
  if (m.role === 'user' && m.docs?.length) {
    const blocks = m.docs
      .map((d) => `【文档：${d.name}】` + '\n' + d.text)
      .join('\n\n')
    text = text ? text + '\n\n' + blocks : blocks
  }
  if (m.role === 'user' && m.images?.length) {
    const content = [{ type: 'text', text: text.trim() || '请分析图片内容' }]
    for (const url of m.images) {
      content.push({ type: 'image_url', image_url: { url } })
    }
    return { role: m.role, content }
  }
  return { role: m.role, content: text }
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
    // 只把已完成的对话传给模型；带图的用户消息转成 OpenAI 视觉格式
    const history = conv.messages.filter((m) => !m.streaming).map(toApiMessage)
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
/* dataURL 图片转 PNG blob：剪贴板写入只认 image/png */
function dataUrlToPngBlob(dataUrl) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = img.width
      canvas.height = img.height
      canvas.getContext('2d').drawImage(img, 0, 0)
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('转换失败'))), 'image/png')
    }
    img.onerror = () => reject(new Error('图片加载失败'))
    img.src = dataUrl
  })
}

/* 点击历史文档标签：重新加回输入框，可直接继续提问 */
function reuseDoc(doc) {
  const input = chatInputRef.value || heroInputRef.value
  if (!input?.addDocs) {
    ElMessage.error('未找到可用的输入框')
    return
  }
  input.addDocs([doc])
  ElMessage.success('已重新附加文档')
}

async function copyMessage(msg) {
  try {
    if (msg.role === 'user' && msg.images?.length) {
      // 带图消息：图片写入剪贴板，可直接 Ctrl+V 粘贴回输入框
      const items = []
      for (const url of msg.images) {
        items.push(new ClipboardItem({ 'image/png': await dataUrlToPngBlob(url) }))
      }
      await navigator.clipboard.write(items)
    } else {
      await navigator.clipboard.writeText(msg.content)
    }
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

/* 编辑用户消息：撤回该条及之后的对话，文字放回输入框（图片不恢复） */
function handleEdit(msgIndex) {
  const conv = chatStore.current
  if (!conv || conv.messages.some((m) => m.streaming)) return
  const text = conv.messages[msgIndex]?.content
  if (!text) return
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
      @open-settings="settingsOpen = true"
    />

    <!-- 系统设置：模型 / API / 采样参数 -->
    <SettingsDialog v-model:open="settingsOpen" />

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
          ref="heroInputRef"
          :deep-think="deepThink"
          :draft="draftText"
          :streaming="streaming"
          @toggle-deep="deepThink = !deepThink"
          @send="handleSend"
          @stop="abortController?.abort()"
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

                <!-- 用户消息里的图片：点击放大预览 -->
                <div v-if="msg.role === 'user' && msg.images?.length" class="msg-images">
                  <el-image
                    v-for="(img, i) in msg.images"
                    :key="i"
                    :src="img"
                    :preview-src-list="msg.images"
                    :initial-index="i"
                    fit="cover"
                    class="msg-img"
                    preview-teleported
                  />
                </div>

                <!-- 用户消息附带的文档 -->
                <div v-if="msg.role === 'user' && msg.docs?.length" class="msg-docs">
                  <span v-for="(doc, i) in msg.docs" :key="i" class="msg-doc">
                    <el-icon><Paperclip /></el-icon>{{ doc.name }}
                    <el-tooltip content="重新附加到输入框" placement="top" :show-after="300">
                      <button class="doc-reuse" type="button" @click.stop="reuseDoc(doc)">
                        <el-icon :size="12"><RefreshRight /></el-icon>
                      </button>
                    </el-tooltip>
                  </span>
                </div>
                <span v-if="msg.role === 'assistant' && !msg.content && !msg.reasoning" class="dots">
                  <i></i><i></i><i></i>
                </span>
                <!-- 助手消息渲染 Markdown；用户消息保持纯文本 -->
                <div v-if="msg.role === 'assistant'" class="text md-content" v-html="renderMarkdown(msg.content)"></div>
                <span v-else class="text">{{ msg.content }}</span>
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
            ref="chatInputRef"
            :deep-think="deepThink"
            :draft="draftText"
            :streaming="streaming"
            compact
            @toggle-deep="deepThink = !deepThink"
            @send="handleSend"
            @stop="abortController?.abort()"
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

/* 用户消息中的图片：缩略图网格，点击放大 */
.msg-images {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.msg-img {
  width: 140px;
  height: 140px;
  border-radius: 10px;
  cursor: zoom-in;
  background: #fff;
}

/* 用户消息附带的文档标签 */
.msg-docs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.msg-doc {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 220px;
  padding: 3px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.28);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 文档标签上的"重新附加"小按钮 */
.doc-reuse {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 0;
  flex: none;
}

.doc-reuse:hover {
  background: rgba(0, 0, 0, 0.12);
}

/* AI 消息：无气泡底色，整块平铺，与标题同左缘 */
.chat-item.assistant .bubble {
  width: 100%;
  padding: 4px 2px;
  color: var(--text);
}

/* ===== Markdown 渲染样式（AI 回复） ===== */
.md-content {
  white-space: normal; /* 覆盖 .bubble 的 pre-wrap，交给 HTML 标签控制换行 */
}

.md-content :deep(h1),
.md-content :deep(h2),
.md-content :deep(h3),
.md-content :deep(h4) {
  margin: 14px 0 8px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text);
}

.md-content :deep(h1) { font-size: 18px; }
.md-content :deep(h2) { font-size: 17px; }
.md-content :deep(h3) { font-size: 16px; }
.md-content :deep(h4) { font-size: 15px; }

.md-content :deep(h1:first-child),
.md-content :deep(h2:first-child),
.md-content :deep(h3:first-child),
.md-content :deep(p:first-child) {
  margin-top: 0;
}

.md-content :deep(p) {
  margin: 8px 0;
}

.md-content :deep(ul),
.md-content :deep(ol) {
  margin: 8px 0;
  padding-left: 22px;
}

.md-content :deep(li) {
  margin: 4px 0;
}

.md-content :deep(strong) {
  font-weight: 600;
}

.md-content :deep(code) {
  background: var(--fill-light);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 13px;
  font-family: Consolas, Monaco, monospace;
}

.md-content :deep(pre) {
  background: #f6f8fa;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin: 10px 0;
  overflow-x: auto;
}

.md-content :deep(pre code) {
  background: transparent;
  padding: 0;
  font-size: 13px;
}

.md-content :deep(table) {
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 13px;
}

.md-content :deep(th),
.md-content :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
}

.md-content :deep(th) {
  background: var(--fill-light);
  font-weight: 600;
}

.md-content :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 12px;
  border-left: 3px solid var(--primary);
  background: var(--fill-light);
  border-radius: 0 6px 6px 0;
  color: var(--text-muted);
}

.md-content :deep(a) {
  color: var(--primary);
  text-decoration: none;
}

.md-content :deep(a:hover) {
  text-decoration: underline;
}

.md-content :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 12px 0;
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
