<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, ChatDotRound, Promotion, Refresh, VideoPlay } from '@element-plus/icons-vue'

import { chatScheduling, optimizeScheduling } from '@/services/schedulingOffloading'
import { renderMarkdown } from '@/utils/markdown'

const router = useRouter()
const loading = ref(false)
const result = ref(null)
const activeTab = ref('decisions')
const chatInput = ref('')
const chatLoading = ref(false)
const messages = ref([])
const form = ref({
  engine: 'research',
  algorithm: 'midterm',
  seed: 41055,
  number_of_jobs: 20,
  population_size: 4,
  generations: 1,
  decision_horizon: 4,
  time_horizon: 20,
})

const metrics = computed(() => result.value?.metrics || {})
const decisions = computed(() => result.value?.decisions || [])
const computeChoices = computed(() => {
  const choices = metrics.value.compute_choices || {}
  return Object.entries(choices).map(([node, count]) => ({ node, count }))
})

function number(value, digits = 2) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : '—'
}

function percent(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(1)}%` : '—'
}

function kindLabel(kind) {
  return { ROUTING: '机器选择', SEQUENCING: '加工排序', OFFLOADING: '计算卸载' }[kind] || kind
}

function kindType(kind) {
  return { ROUTING: 'primary', SEQUENCING: 'success', OFFLOADING: 'warning' }[kind] || 'info'
}

async function sendChat() {
  const query = chatInput.value.trim()
  if (!query || chatLoading.value) return
  messages.value.push({ type: 'user', content: query })
  chatInput.value = ''
  chatLoading.value = true
  try {
    const response = await chatScheduling(query)
    messages.value.push({ type: 'assistant', content: response.answer })
    if (response.run_result) result.value = response.run_result
  } catch (error) {
    messages.value.push({ type: 'error', content: error.message })
  } finally {
    chatLoading.value = false
  }
}

function useSample(text) {
  chatInput.value = text
}

async function run() {
  loading.value = true
  result.value = null
  try {
    result.value = await optimizeScheduling(form.value)
    ElMessage.success('联合调度实验已完成')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

function reset() {
  form.value = {
    engine: 'research', algorithm: 'midterm', seed: 41055, number_of_jobs: 20,
    population_size: 4, generations: 1, decision_horizon: 4, time_horizon: 20,
  }
  result.value = null
  messages.value = []
  chatInput.value = ''
}
</script>

<template>
  <div class="schedule-page">
    <!-- 与需求预测、知识库页面保持一致的顶栏 -->
    <header class="top-head">
      <div class="bar-left">
        <el-button text bg size="small" @click="router.push('/')">
          <el-icon style="margin-right: 4px"><Back /></el-icon>返回对话
        </el-button>
      </div>
      <div class="head-title">生产调度与计算卸载智能体</div>
      <div class="bar-right">
        <el-button text bg size="small" :disabled="loading" @click="reset">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>恢复默认
        </el-button>
      </div>
    </header>

    <main class="body">
      <section class="intro">
        <div>
          <h1>联合调度实验</h1>
          <p>为工序选择机器、安排加工顺序，并为在线质检任务选择计算位置。</p>
        </div>
        <el-tag size="small" effect="plain" type="info">仿真实验数据</el-tag>
      </section>

      <section class="chat-card">
        <div class="chat-card-head">
          <div class="agent-mark"><el-icon><ChatDotRound /></el-icon></div>
          <div>
            <b>调度智能体</b>
            <span>描述任务后，智能体会调用算法计算并解释结果</span>
          </div>
        </div>

        <div v-if="messages.length" class="message-list">
          <div v-for="(message, index) in messages" :key="index" :class="['message-row', message.type]">
            <div v-if="message.type === 'user'" class="user-card">{{ message.content }}</div>
            <div v-else class="assistant-card" :class="{ error: message.type === 'error' }">
              <div class="assistant-label">
                <el-icon><ChatDotRound /></el-icon>
                <span>{{ message.type === 'error' ? '运行失败' : '调度智能体' }}</span>
              </div>
              <div v-if="message.type === 'assistant'" class="assistant-content md-content" v-html="renderMarkdown(message.content)"></div>
              <div v-else class="assistant-content">{{ message.content }}</div>
            </div>
          </div>
          <div v-if="chatLoading" class="thinking-card">
            <span></span><span></span><span></span>
            <em>正在理解请求并调用调度算法</em>
          </div>
        </div>

        <div v-else class="sample-prompts">
          <button @click="useSample('使用默认场景生成一个联合调度与计算卸载方案。')">使用默认场景生成方案</button>
          <button @click="useSample('为20个工件运行联合调度，并解释质检超时情况。')">运行20工件并分析质检</button>
          <button @click="useSample('什么是生产调度与计算卸载的耦合？')">解释调度与卸载耦合</button>
        </div>

        <div class="chat-composer">
          <el-input
            v-model="chatInput"
            type="textarea"
            :rows="2"
            resize="none"
            placeholder="例如：使用20个工件和随机种子41055生成联合调度方案"
            @keydown.enter.exact.prevent="sendChat"
          />
          <div class="composer-foot">
            <span>Enter 发送，Shift + Enter 换行</span>
            <el-button type="primary" circle :icon="Promotion" :loading="chatLoading" :disabled="!chatInput.trim()" @click="sendChat" />
          </div>
        </div>
      </section>

      <section class="config-panel">
        <div class="section-head">
          <div>
            <span class="section-title">实验配置</span>
            <span class="section-sub">动态柔性作业车间仿真场景</span>
          </div>
        </div>

        <el-form label-position="top" :model="form" class="config-form">
          <div class="form-grid">
            <el-form-item label="决策策略">
              <el-input model-value="联合调度与计算卸载策略" disabled />
            </el-form-item>
            <el-form-item label="随机种子">
              <el-input-number v-model="form.seed" :min="0" :controls="false" />
            </el-form-item>
            <el-form-item label="工件数量">
              <el-input-number v-model="form.number_of_jobs" :min="1" :max="100" />
            </el-form-item>
          </div>
        </el-form>

        <div class="scene-summary">
          <span>3个工作中心</span><i></i><span>6台机器</span><i></i>
          <span>每道工序3台候选机器</span><i></i><span>本地、双边缘、云端</span>
        </div>

        <div class="run-row">
          <span>相同随机种子会生成相同场景，便于比较不同算法。</span>
          <el-button type="primary" :icon="VideoPlay" :loading="loading" @click="run">
            {{ loading ? '正在联合优化' : '开始优化' }}
          </el-button>
        </div>
      </section>

      <section v-if="!result" class="empty-state">
        <div class="flow-line">
          <div><b>生产状态</b><span>工件、工序、机器</span></div>
          <strong>→</strong>
          <div><b>联合决策</b><span>路由、排序、卸载</span></div>
          <strong>→</strong>
          <div><b>执行结果</b><span>效率与质检指标</span></div>
        </div>
        <p>点击“开始优化”运行一次完整的仿真实验。</p>
      </section>

      <template v-else>
        <el-alert
          v-for="warning in result.warnings"
          :key="warning"
          class="warning"
          :title="warning"
          type="warning"
          show-icon
          :closable="false"
        />

        <section class="result-head">
          <div class="result-status">
            <span class="status-dot"></span>
            <b>运行完成</b>
            <span>{{ result.elapsed_seconds }} 秒</span>
          </div>
          <div class="result-meta">
            <el-tag size="small" :type="result.is_demo ? 'warning' : 'success'" effect="light">
              联合仿真
            </el-tag>
            <code>{{ result.run_id.slice(0, 8) }}</code>
          </div>
        </section>

        <section class="metrics-grid">
          <article><span>最大完工时间</span><b>{{ number(metrics.makespan) }}</b><small>生产效率</small></article>
          <article><span>总拖期</span><b>{{ number(metrics.total_tardiness) }}</b><small>交期表现</small></article>
          <article><span>机器利用率</span><b>{{ percent(metrics.machine_utilization) }}</b><small>设备负载</small></article>
          <article><span>平均质检阻塞</span><b>{{ number(metrics.mean_blocking) }}</b><small>质量门等待</small></article>
          <article><span>质检超时率</span><b>{{ percent(metrics.inspection_timeout_rate) }}</b><small>计算及时性</small></article>
          <article><span>最大超时</span><b>{{ number(metrics.max_timeout_lateness) }}</b><small>尾部风险</small></article>
        </section>

        <section class="detail-panel">
          <el-tabs v-model="activeTab">
            <el-tab-pane label="决策过程" name="decisions">
              <el-table :data="decisions" height="390" stripe>
                <el-table-column prop="time" label="仿真时刻" width="115" />
                <el-table-column label="决策类型" width="125">
                  <template #default="scope">
                    <el-tag size="small" :type="kindType(scope.row.kind)" effect="light">
                      {{ kindLabel(scope.row.kind) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="action" label="算法动作" min-width="260" />
              </el-table>
            </el-tab-pane>
            <el-tab-pane label="计算节点" name="compute">
              <el-table :data="computeChoices" stripe>
                <el-table-column prop="node" label="计算位置" />
                <el-table-column prop="count" label="质检任务数" />
              </el-table>
            </el-tab-pane>
            <el-tab-pane label="场景参数" name="scenario">
              <el-descriptions :column="2" border>
                <el-descriptions-item label="随机种子">{{ result.scenario.seed }}</el-descriptions-item>
                <el-descriptions-item label="工件数量">{{ result.scenario.number_of_jobs }}</el-descriptions-item>
                <el-descriptions-item label="工作中心">{{ result.scenario.number_of_work_centers }}</el-descriptions-item>
                <el-descriptions-item label="机器数量">{{ result.scenario.machine_count }}</el-descriptions-item>
                <el-descriptions-item label="柔性模式">{{ result.scenario.machine_flexibility }}</el-descriptions-item>
                <el-descriptions-item label="决策总数">{{ result.decision_summary.total }}</el-descriptions-item>
              </el-descriptions>
            </el-tab-pane>
          </el-tabs>
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.schedule-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
  color: var(--text);
}

/* 顶栏与需求预测、知识库页面相同 */
.top-head {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 20px 6px;
}

.bar-left { display: flex; gap: 4px; }
.bar-right { margin-left: auto; }
.head-title {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  width: 100%;
  max-width: 900px;
  margin: 0 auto;
  padding: 16px 20px 36px;
  scrollbar-width: thin;
  scrollbar-color: #d3d7de transparent;
}
.body::-webkit-scrollbar { width: 6px; }
.body::-webkit-scrollbar-thumb { background: #d3d7de; border-radius: 3px; }

.intro {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin: 10px 2px 18px;
}
.intro h1 { font-size: 20px; margin: 0 0 7px; }
.intro p { color: var(--text-muted); font-size: 13px; line-height: 1.6; }

/* 自然语言交互使用卡片容器，结果仍由真实算法产生 */
.chat-card {
  margin-bottom: 16px;
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg);
  box-shadow: var(--shadow);
}
.chat-card-head { display: flex; align-items: center; gap: 11px; margin-bottom: 14px; }
.chat-card-head > div:last-child { display: flex; flex-direction: column; gap: 3px; }
.chat-card-head span { color: var(--text-muted); font-size: 12px; }
.agent-mark { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 10px; background: var(--primary-light); color: var(--primary); }
.message-list { display: flex; flex-direction: column; gap: 12px; max-height: 360px; overflow-y: auto; padding: 4px 2px 12px; }
.message-row { display: flex; }
.message-row.user { justify-content: flex-end; }
.user-card { max-width: 76%; padding: 10px 14px; border-radius: 16px 16px 4px 16px; background: var(--primary-light); line-height: 1.6; }
.assistant-card { width: 100%; padding: 14px 16px; border: 1px solid var(--border); border-radius: var(--radius-md); background: #fff; }
.assistant-card.error { border-color: #fecaca; background: #fff8f8; color: #b91c1c; }
.assistant-label { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; color: var(--primary); font-size: 12px; font-weight: 600; }
.assistant-content { line-height: 1.7; font-size: 13.5px; }
.thinking-card { align-self: flex-start; display: flex; align-items: center; gap: 5px; padding: 10px 13px; border: 1px solid var(--border); border-radius: var(--radius-md); }
.thinking-card span { width: 5px; height: 5px; border-radius: 50%; background: var(--primary); animation: pulse 1.2s infinite ease-in-out; }
.thinking-card span:nth-child(2) { animation-delay: .15s; }.thinking-card span:nth-child(3) { animation-delay: .3s; }
.thinking-card em { margin-left: 5px; color: var(--text-muted); font-size: 12px; font-style: normal; }
@keyframes pulse { 0%, 60%, 100% { opacity: .25; transform: translateY(0); } 30% { opacity: 1; transform: translateY(-3px); } }
.sample-prompts { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.sample-prompts button { padding: 7px 11px; border: 1px solid var(--border); border-radius: 16px; background: var(--fill-light); color: #606875; cursor: pointer; font-size: 12px; }
.sample-prompts button:hover { border-color: var(--primary); color: var(--primary); }
.chat-composer { padding: 10px 12px 8px; border: 2px solid transparent; border-radius: var(--radius-md); background: linear-gradient(var(--bg), var(--bg)) padding-box, linear-gradient(90deg, #ff8bd2, #b57bee, #5b8def) border-box; }
.chat-composer :deep(.el-textarea__inner) { padding: 2px; border: 0; box-shadow: none; background: transparent; }
.composer-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 5px; }
.composer-foot span { color: var(--text-muted); font-size: 11.5px; }

.config-panel,
.detail-panel {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg);
}
.config-panel { padding: 20px 22px 18px; }
.section-head { display: flex; align-items: center; margin-bottom: 16px; }
.section-title { font-weight: 600; }
.section-sub { margin-left: 10px; color: var(--text-muted); font-size: 12px; }
.form-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.config-form :deep(.el-form-item) { margin-bottom: 14px; }
.config-form :deep(.el-form-item__label) { font-size: 12.5px; color: #606875; }
.form-grid :deep(.el-select), .form-grid :deep(.el-input-number) { width: 100%; }

.scene-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
  padding: 10px 12px;
  border-radius: 7px;
  background: var(--fill-light);
  color: #606875;
  font-size: 12.5px;
}
.scene-summary i { width: 3px; height: 3px; border-radius: 50%; background: #aab1bc; }
.run-row { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; }
.run-row > span { color: var(--text-muted); font-size: 12px; }

.empty-state {
  min-height: 230px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
.flow-line { display: flex; align-items: center; gap: 14px; }
.flow-line > div {
  min-width: 165px;
  padding: 16px;
  text-align: center;
  border-radius: var(--radius-md);
  background: var(--fill-light);
}
.flow-line b, .flow-line span { display: block; }
.flow-line b { margin-bottom: 6px; color: var(--text); font-size: 13px; }
.flow-line span { font-size: 12px; }
.flow-line strong { color: #c4c9d1; font-weight: 400; }
.empty-state > p { margin-top: 18px; font-size: 12.5px; }

.warning { margin-top: 14px; }
.result-head { display: flex; justify-content: space-between; align-items: center; margin: 22px 2px 12px; }
.result-status, .result-meta { display: flex; align-items: center; gap: 8px; }
.result-status > span, .result-meta code { color: var(--text-muted); font-size: 12px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: #27b67a; }

.metrics-grid { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--border); border-radius: var(--radius-md); overflow: hidden; }
.metrics-grid article { padding: 16px 18px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.metrics-grid article:nth-child(3n) { border-right: 0; }
.metrics-grid article:nth-child(n+4) { border-bottom: 0; }
.metrics-grid span, .metrics-grid small { display: block; color: var(--text-muted); font-size: 12px; }
.metrics-grid b { display: block; margin: 8px 0 5px; font-size: 21px; font-variant-numeric: tabular-nums; }
.detail-panel { margin-top: 16px; padding: 10px 18px 18px; }

@media (max-width: 760px) {
  .head-title { display: none; }
  .form-grid { grid-template-columns: 1fr 1fr; }
  .metrics-grid { grid-template-columns: repeat(2, 1fr); }
  .metrics-grid article, .metrics-grid article:nth-child(3n), .metrics-grid article:nth-child(n+4) { border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
  .metrics-grid article:nth-child(2n) { border-right: 0; }
  .metrics-grid article:nth-last-child(-n+2) { border-bottom: 0; }
  .flow-line { gap: 6px; }.flow-line > div { min-width: 0; padding: 12px 8px; }
}
</style>
