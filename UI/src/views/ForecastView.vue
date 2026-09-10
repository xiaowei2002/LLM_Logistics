<script setup>
import { computed, nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Back, ChatDotRound, Check, Promotion, RefreshLeft } from '@element-plus/icons-vue'

import { predictDemand, resumeDemand } from '@/services/forecast'
import { renderMarkdown } from '@/utils/markdown'

const router = useRouter()

/* 对话流消息类型：user 用户输入 / assistant 最终回答 / card 人在环提问卡片 / error 错误 */
const messages = ref([])
const input = ref('')
const loading = ref(false)
const threadId = ref(null)
const flowEl = ref(null)

/* 是否存在未回应的提问卡片：此时主输入框禁用，必须先回答卡片 */
const pendingCard = computed(() => messages.value.find((m) => m.type === 'card' && !m.answered))

/* 后端 reason 代码转中文标签 */
const REASON_TEXT = {
  empty_query: '请求为空',
  missing_forecast_time: '缺少具体时刻',
  missing_forecast_timestamp: '缺少预测时间',
}

function reasonText(reason) {
  return REASON_TEXT[reason] || reason || '需要补充信息'
}

/* 卡片问题的前端引导文案：后端的 question 偏死板。
   注意正则只检查首次输入，卡片回答会直接拼给 LLM，
   自然语言时间（如"1月17号 中午12点"）是允许的 */
const QUESTION_HINT = {
  missing_forecast_timestamp:
    '没有识别到预测时间。你可以用自然语言描述，如：1月17号 中午12点；也可以点击下方按钮填入示例时间。',
  missing_forecast_time:
    '已识别到日期，但缺少具体时刻。补充任意写法的时刻即可，如：中午12点。',
}

/* 与后端 human_review 相同的时间预检：仅用于发送前提示，不拦截 */
const DATETIME_PATTERNS = [
  /\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{1,2}(?::\d{1,2})?/,
  /\d{4}年\d{1,2}月\d{1,2}日?\s*\d{1,2}(?:点|时|:\d{1,2}(?::\d{1,2})?)/,
]

const timeDetected = computed(() => DATETIME_PATTERNS.some((re) => re.test(input.value)))

/* 输入框下方提示：预检到缺时间时提前说明会发生什么 */
const sendHint = computed(() => {
  if (pendingCard.value) return '请先回应上方的提问'
  if (input.value.trim() && !timeDetected.value) {
    const last = messages.value[messages.value.length - 1]
    /* 上一条是 AI 正文回复：说明智能体在正文里提了问题，
       但管道不支持多轮追问（重新发送会丢失上下文并再次过正则门） */
    if (last?.type === 'assistant') {
      return '智能体不保留对话上下文：建议点「新预测」，一次性描述完整需求（含年份和时刻）'
    }
    return '未检测到具体时间：可直接发送，智能体会向你确认'
  }
  return 'Enter 发送，Shift + Enter 换行'
})

function scrollToBottom() {
  nextTick(() => {
    if (flowEl.value) flowEl.value.scrollTop = flowEl.value.scrollHeight
  })
}

/* 统一处理 predict / resume 的返回：出最终结果，或转入人在环提问 */
function applyResult(res) {
  threadId.value = res.thread_id
  if (res.status === 'needs_human_input') {
    messages.value.push({
      type: 'card',
      question: res.question,
      reason: res.reason,
      draft: '',
      answered: false,
      answer: '',
    })
    scrollToBottom()
    return
  }
  messages.value.push({ type: 'assistant', content: res.answer })
  scrollToBottom()
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value || pendingCard.value) return
  input.value = ''
  messages.value.push({ type: 'user', content: text })
  loading.value = true
  scrollToBottom()
  try {
    applyResult(await predictDemand(text, threadId.value))
  } catch (err) {
    messages.value.push({ type: 'error', content: err.message })
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

/* 回答提问卡片：标记已答、回显用户消息，调 resume 让智能体从断点继续 */
async function submitAnswer(card) {
  const text = (card.draft || '').trim()
  if (!text || loading.value) return
  card.answered = true
  card.answer = text
  messages.value.push({ type: 'user', content: text })
  loading.value = true
  scrollToBottom()
  try {
    applyResult(await resumeDemand(threadId.value, text))
  } catch (err) {
    messages.value.push({ type: 'error', content: err.message })
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

/* Enter 提交：中文输入法选词时的 Enter 不算发送 */
function handleEnterKey(event) {
  if (event.isComposing) return
  send()
}

/* 快捷回答：Experiment 3 示例数据的时间范围为 2026-01-02 ~ 2026-01-31，
   当前真实时间超出可预测范围，故填数据范围内的示例时刻 */
const SAMPLE_TIME = '2026-01-30 12:00:00'

function fillSampleTime(card) {
  card.draft = SAMPLE_TIME
}

/* 清空，开始新一轮预测（thread 也重置） */
function reset() {
  if (loading.value) return
  messages.value = []
  threadId.value = null
  input.value = ''
}
</script>

<template>
  <div class="forecast-page">
    <!-- 顶栏：返回 / 新预测 + 居中标题 -->
    <header class="top-head">
      <div class="bar-left">
        <el-button text bg size="small" @click="router.push('/')">
          <el-icon style="margin-right: 4px"><Back /></el-icon>返回对话
        </el-button>
        <el-button text bg size="small" :disabled="!messages.length || loading" @click="reset">
          <el-icon style="margin-right: 4px"><RefreshLeft /></el-icon>新预测
        </el-button>
      </div>
      <div class="head-title">需求预测智能体</div>
    </header>

    <!-- 对话流 -->
    <main ref="flowEl" class="flow">
      <div class="flow-inner">
        <template v-for="(m, i) in messages" :key="i">
          <!-- 用户消息：靠右淡蓝胶囊，与对话页一致 -->
          <div v-if="m.type === 'user'" class="row user">
            <div class="bubble">{{ m.content }}</div>
          </div>

          <!-- 智能体最终回答：无气泡平铺，与对话页一致 -->
          <div v-else-if="m.type === 'assistant'" class="row bot">
            <div class="bot-text md-content" v-html="renderMarkdown(m.content)"></div>
          </div>

          <!-- 人在环提问卡片：智能体暂停等待补充信息 -->
          <div v-else-if="m.type === 'card'" class="row bot">
            <div class="ask-card">
              <div class="ask-head">
                <el-icon class="ask-icon"><ChatDotRound /></el-icon>
                <span class="ask-title">智能体需要你补充信息</span>
                <el-tag size="small" type="warning" effect="light">{{ reasonText(m.reason) }}</el-tag>
              </div>
              <div class="ask-q">{{ QUESTION_HINT[m.reason] || m.question }}</div>
              <template v-if="!m.answered">
                <div class="ask-chips">
                  <el-button size="small" round @click="fillSampleTime(m)">填入示例时间（数据范围内）</el-button>
                </div>
                <div class="ask-input">
                  <el-input
                    v-model="m.draft"
                    placeholder="用自然语言回答即可，如：1月17号 中午12点"
                    @keydown.enter.exact.prevent="submitAnswer(m)"
                  />
                  <el-button type="primary" :disabled="!m.draft.trim()" @click="submitAnswer(m)">提交</el-button>
                </div>
              </template>
              <div v-else class="ask-done"><el-icon><Check /></el-icon>已回答：{{ m.answer }}</div>
            </div>
          </div>

          <!-- 错误提示 -->
          <div v-else-if="m.type === 'error'" class="row user">
            <div class="bubble error">{{ m.content }}</div>
          </div>
        </template>

        <!-- 等待动画：三个跳动圆点，与对话页一致 -->
        <div v-if="loading" class="row bot">
          <span class="dots"><i></i><i></i><i></i></span>
        </div>

        <!-- 空状态引导：两个示例分别触发"直接预测"和"人在环提问" -->
        <div v-if="!messages.length && !loading" class="empty-hint">
          <div class="empty-title">物料需求预测</div>
          <p>用一句话描述你的预测需求；缺少预测时间时，智能体会主动向你确认。<br />示例数据可预测范围：2026-01-02 07:00 ~ 2026-01-31 05:00。</p>
          <div class="sample" @click="input = '请预测未来两小时 M001 到 M008 的物料需求数量。'">
            请预测未来两小时 M001 到 M008 的物料需求数量。<span class="sample-tag">触发提问</span>
          </div>
          <div
            class="sample"
            @click="input = '请预测 2026-01-30 12:00:00 之后未来两小时 M001 到 M008 的具体物料需求数量。'"
          >
            请预测 2026-01-30 12:00:00 之后未来两小时 M001 到 M008 的物料需求数量。<span class="sample-tag ok">直接预测</span>
          </div>
        </div>
      </div>
    </main>

    <!-- 底部输入区：沿用对话页的渐变描边输入框 -->
    <footer class="input-dock">
      <div class="composer-box" :class="{ locked: pendingCard }">
        <el-input
          v-model="input"
          type="textarea"
          :rows="1"
          resize="none"
          :disabled="!!pendingCard"
          :placeholder="pendingCard ? '请先回应上方的提问' : '描述你的预测需求…'"
          class="composer-input"
          @keydown.enter.exact.prevent="handleEnterKey"
        />
        <div class="composer-foot">
          <span class="send-hint">{{ sendHint }}</span>
          <el-button
            type="primary"
            circle
            class="send-btn"
            :disabled="loading || !!pendingCard || !input.trim()"
            @click="send"
          >
            <el-icon><Promotion /></el-icon>
          </el-button>
        </div>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.forecast-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
}

/* ===== 顶栏：无边框白底，标题居中，与对话页同一气质 ===== */
.top-head {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 20px 6px;
}

.bar-left {
  display: flex;
  gap: 4px;
}

.head-title {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

/* ===== 对话流 ===== */
.flow {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  width: 100%;
  padding: 20px 16px 8px;
  scrollbar-width: thin;
  scrollbar-color: #d3d7de transparent;
}

.flow::-webkit-scrollbar {
  width: 6px;
}

.flow::-webkit-scrollbar-thumb {
  background: #d3d7de;
  border-radius: 3px;
}

.flow::-webkit-scrollbar-track {
  background: transparent;
}

.flow-inner {
  max-width: 760px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
}

/* 组内间距 16px；新一轮用户消息与上一答拉开到 24px */
.row + .row {
  margin-top: 16px;
}

.row.user:not(:first-child) {
  margin-top: 24px;
}

.row {
  display: flex;
}

.row.user {
  flex-direction: column;
  align-items: flex-end;
  padding-right: 10px;
}

.row.bot {
  flex-direction: column;
  align-items: stretch;
}

/* 用户消息：淡蓝胶囊、无边框，与对话页一致 */
.row.user .bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 20px;
  background: var(--primary-light);
  color: var(--text);
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 错误提示：淡红胶囊 */
.row.user .bubble.error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #b91c1c;
}

/* 智能体回答：无气泡平铺，整块宽度与标题同左缘 */
.bot-text {
  width: 100%;
  padding: 4px 2px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
  word-break: break-word;
}

/* ===== 人在环提问卡片 ===== */
.ask-card {
  background: #fffbeb;
  border: 1px solid #f3e5c0;
  border-left: 3px solid #f59e0b;
  border-radius: 14px;
  padding: 14px 16px;
  margin: 4px 0 4px 2px;
  box-shadow: var(--shadow);
}

.ask-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ask-icon {
  color: #f59e0b;
}

.ask-title {
  font-weight: 600;
  font-size: 14px;
  color: #92400e;
}

.ask-q {
  margin-top: 8px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--text);
}

.ask-chips {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}

.ask-input {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}

.ask-done {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #16a34a;
}

/* ===== 等待动画：三个跳动圆点 ===== */
.dots {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  height: 18px;
  padding: 2px;
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

/* ===== 空状态引导 ===== */
.empty-hint {
  margin: 10vh auto 0;
  max-width: 560px;
  text-align: center;
}

.empty-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text);
}

.empty-hint > p {
  margin: 8px 0 20px;
  font-size: 13px;
  color: var(--text-muted);
}

.sample {
  position: relative;
  margin: 0 auto 10px;
  padding: 10px 14px;
  max-width: 520px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.sample:hover {
  border-color: var(--primary);
  box-shadow: var(--shadow);
}

.sample-tag {
  position: absolute;
  right: 10px;
  top: -9px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  background: #fffbeb;
  border: 1px solid #f3e5c0;
  color: #92400e;
}

.sample-tag.ok {
  background: var(--primary-light);
  border-color: #c1d0fa;
  color: var(--primary);
}

/* ===== 底部输入区：沿用对话页的渐变描边输入框 ===== */
.input-dock {
  max-width: 760px;
  width: 100%;
  margin: 0 auto;
  padding: 8px 0 16px;
}

.composer-box {
  border: 2px solid transparent;
  border-radius: 16px;
  background:
    linear-gradient(var(--bg), var(--bg)) padding-box,
    linear-gradient(90deg, #ff8bd2, #b57bee, #5b8def) border-box;
  box-shadow: var(--shadow);
  padding: 12px 16px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 有待回应卡片时：灰化边框提示先回答 */
.composer-box.locked {
  background:
    linear-gradient(var(--bg), var(--bg)) padding-box,
    linear-gradient(90deg, var(--border), var(--border)) border-box;
}

.composer-input :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  background: transparent;
  resize: none;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
  padding: 2px 4px;
  min-height: 26px;
}

.composer-input :deep(.el-textarea__inner)::placeholder {
  color: var(--text-placeholder);
}

.composer-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.send-hint {
  color: var(--text-placeholder);
  font-size: 12px;
}

.send-btn {
  width: 32px;
  height: 32px;
  padding: 0;
}

/* ===== Markdown 渲染样式（与对话页一致） ===== */
.md-content {
  white-space: normal;
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
</style>
