<script setup>
import { nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppHero from '@/components/AppHero.vue'
import TaskInput from '@/components/TaskInput.vue'
import { useAuthStore } from '@/stores/auth'
import { streamChat } from '@/services/llm'

const router = useRouter()
const authStore = useAuthStore()

/** 对话记录：{ role, content, reasoning?, reasoningOpen?, thinkSeconds?, streaming? } */
const messages = ref([])
const chatListRef = ref(null)
const deepThink = ref(true)
let abortController = null

async function scrollToBottom() {
  await nextTick()
  const el = chatListRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function handleSend(text) {
  if (messages.value.some((m) => m.streaming)) return

  abortController?.abort()
  abortController = new AbortController()

  messages.value.push({ role: 'user', content: text })
  messages.value.push({
    role: 'assistant',
    content: '',
    reasoning: '',
    reasoningOpen: true,
    thinkSeconds: null,
    streaming: true,
  })
  // 取 push 后的响应式代理对象，直接改原始对象不会触发页面更新
  const reply = messages.value[messages.value.length - 1]
  const thinkStart = Date.now()
  scrollToBottom()

  try {
    // 只把已完成的对话传给模型
    const history = messages.value
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
    scrollToBottom()
  }
}

function handleLogout() {
  abortController?.abort()
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

    <main class="main" :class="{ 'has-chat': messages.length }">
      <!-- 未开始对话：居中展示 Logo 和输入框 -->
      <template v-if="!messages.length">
        <AppHero />
        <TaskInput
          :deep-think="deepThink"
          @toggle-deep="deepThink = !deepThink"
          @send="handleSend"
        />
      </template>

      <!-- 开始对话后：聊天记录在上方滚动，输入框固定底部 -->
      <template v-else>
        <section class="chat-panel">
          <div ref="chatListRef" class="chat-scroll">
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
              </div>
            </div>
          </div>
        </section>

        <div class="input-dock">
          <TaskInput
            :deep-think="deepThink"
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
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px 32px 48px;
  /* 固定为视口高度：超出部分由聊天记录区内部滚动，输入框始终可见 */
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
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
  min-height: 0;
}

/* 开始对话后：记录区占满剩余空间并内部滚动，输入框贴底 */
.main.has-chat {
  justify-content: flex-start;
}

/* 消息面板：可见的卡片外壳（不滚动） */
.chat-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  max-width: 760px;
  width: 100%;
  margin: 16px auto;
  background: var(--fill-light);
  border: 1px solid #d7dbe2;
  border-radius: var(--radius-lg);
  overflow: hidden;
}

/* 面板内的滚动区 */
.chat-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
}

/* 消息列表：margin-top:auto 让消息少时贴底、多时可正常向上滚动 */
.chat-inner {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.chat-item {
  display: flex;
}

.input-dock {
  max-width: 760px;
  width: 100%;
  margin: 0 auto;
  padding-bottom: 8px;
}

.chat-item {
  display: flex;
}

.chat-item.user {
  justify-content: flex-end;
}

.chat-item.assistant {
  justify-content: flex-start;
}

.bubble {
  max-width: 85%;
  padding: 10px 14px;
  border-radius: var(--radius-lg);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-item.user .bubble {
  background: var(--primary);
  color: #fff;
  border-bottom-right-radius: 4px;
}

.chat-item.assistant .bubble {
  background: #fff;
  border: 1px solid var(--border);
  color: var(--text);
  border-bottom-left-radius: 4px;
}

/* 深度思考过程（DeepSeek 风格：可折叠） */
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
  border-left: 3px solid var(--border);
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

/* 思考中时内容区自动增长到较高上限 */
.reasoning-box:has(+ .text:empty) .reasoning-body,
.reasoning-body:only-child {
  max-height: 240px;
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
</style>
