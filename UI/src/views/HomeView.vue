<script setup>
import { nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppHero from '@/components/AppHero.vue'
import TaskInput from '@/components/TaskInput.vue'
import { useAuthStore } from '@/stores/auth'
import { streamChat } from '@/services/llm'

const router = useRouter()
const authStore = useAuthStore()

/** 对话记录：{ role: 'user' | 'assistant', content: string, streaming?: boolean } */
const messages = ref([])
const chatListRef = ref(null)
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
  messages.value.push({ role: 'assistant', content: '', reasoning: '', streaming: true })
  // 取 push 后的响应式代理对象，直接改原始对象不会触发页面更新
  const reply = messages.value[messages.value.length - 1]
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
        if (content) reply.content += content
        scrollToBottom()
      },
      abortController.signal,
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
        <TaskInput @send="handleSend" />
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
                  <template v-if="msg.role === 'assistant' && !msg.content">
                    <div v-if="msg.reasoning" class="reasoning">{{ msg.reasoning }}</div>
                    <span class="dots"><i></i><i></i><i></i></span>
                  </template>
                  <template v-else>
                    <div v-if="msg.reasoning && msg.streaming" class="reasoning collapsed">
                      已深度思考（{{ msg.reasoning.length }} 字）
                    </div>
                    <span class="text">{{ msg.content }}</span>
                    <span v-if="msg.streaming && msg.content" class="caret"></span>
                  </template>
                </div>
              </div>
            </div>
          </div>
        </section>

        <div class="input-dock">
          <TaskInput @send="handleSend" />
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

/* 思考过程：灰色小字 */
.reasoning {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.6;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--border);
  max-height: 150px;
  overflow-y: auto;
  white-space: pre-wrap;
}

.reasoning.collapsed {
  max-height: none;
  overflow: visible;
  margin-bottom: 0;
  padding-bottom: 0;
  border-bottom: none;
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
