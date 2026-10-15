<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Check, CopyDocument, Promotion, Setting } from '@element-plus/icons-vue'

import { chatScheduling, resumeScheduling } from '@/services/schedulingOffloading'
import { renderMarkdown } from '@/utils/markdown'
import AgentContextPanel from '@/components/AgentContextPanel.vue'
import AppNav from '@/components/AppNav.vue'
import ResultInsights from '@/components/ResultInsights.vue'
import '@/assets/styles/agent-workspace.css'

const flowEl = ref(null)
const loading = ref(false)
// 与需求预测一致：文字和图表数据放在同一条最终回答里，一起展示。
const result = computed(() => {
  if (loading.value || pendingCard.value) return null
  return [...messages.value].reverse().find(message => message.type === 'assistant')?.runResult || null
})
const activeTab = ref('decisions')
const input = ref('')
const messages = ref([])
const threadId = ref(null)
const metrics = computed(() => result.value?.metrics || {})
const decisions = computed(() => result.value?.decisions || [])
const computeChoices = computed(() => Object.entries(metrics.value.compute_choices || {}).map(([node, count]) => ({ node, count })))
const pendingCard = computed(() => messages.value.find((message) => message.type === 'card' && !message.answered))


function cardTitle(reason) {
  return {
    missing_scheduling_parameters: '请补充本次场景参数',
    unsupported_scope: '请选择要执行的调度任务',
    invalid_job_count: '请填写有效工件数量',
    invalid_resource_count: '请填写有效资源数量',
    invalid_seed: '请修正随机种子',
    tool_execution_error: '请选择如何重新运行',
  }[reason] || '智能体需要你确认'
}

function cardPlaceholder(reason) {
  return {
    missing_scheduling_parameters: '例如：18个工件、7台机器、3个边缘节点，随机种子41055',
    unsupported_scope: '也可以重新描述调度、排产或计算卸载需求',
    invalid_job_count: '例如：安排10个工件',
    invalid_resource_count: '例如：使用8台机器、3个边缘节点生成调度方案',
    invalid_seed: '输入0到2147483647之间的整数',
    tool_execution_error: '输入新的随机种子或重试要求',
  }[reason] || '请输入补充信息'
}

function cardActions(reason) {
  return {
    missing_scheduling_parameters: ['填写默认场景示例'],
    unsupported_scope: [
      '使用默认场景生成调度方案',
      '解释调度方案中的主要指标',
      '介绍生产调度与计算卸载的决策过程',
    ],
    invalid_job_count: ['安排10个工件并继续'],
    invalid_resource_count: ['使用6台机器、2个边缘节点生成调度方案'],
    invalid_seed: ['使用默认随机种子41055重新运行'],
    tool_execution_error: ['使用默认场景重新运行'],
  }[reason] || []
}

function chooseCardAction(card, action) {
  card.draft = action === '填写默认场景示例'
    ? '请为20个工件、6台机器、2个边缘节点生成调度方案，随机种子41055'
    : action
}

function applyChatResult(response) {
  threadId.value = response.thread_id
  if (response.status === 'needs_human_input') {
    // 等待补充信息时尚无本轮结果，避免把上一轮方案显示在新卡片下方。
    messages.value.push({ type: 'card', reason: response.reason, question: response.question, draft: '', answered: false, answer: '' })
    return
  }
  messages.value.push({ type: 'assistant', content: response.answer, runResult: response.run_result || null })
}

watch([messages, result], scrollToBottom, { deep: true })

async function scrollToBottom() {
  await nextTick()
  if (flowEl.value) flowEl.value.scrollTop = flowEl.value.scrollHeight
}

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

function fillSample(text) {
  input.value = text
}

async function send() {
  const query = input.value.trim()
  if (!query || loading.value || pendingCard.value) return
  messages.value.push({ type: 'user', content: query })
  input.value = ''
  loading.value = true
  try {
    applyChatResult(await chatScheduling(query, threadId.value))
  } catch (error) {
    messages.value.push({ type: 'error', content: error.message })
  } finally {
    loading.value = false
  }
}

async function submitAnswer(card) {
  const answer = card.draft.trim()
  if (!answer || loading.value) return
  card.answered = true
  card.answer = answer
  messages.value.push({ type: 'user', content: answer })
  loading.value = true
  try {
    applyChatResult(await resumeScheduling(threadId.value, answer))
  } catch (error) {
    card.answered = false
    messages.value.push({ type: 'error', content: `${error.message}；如果后端刚重启，请点击“新调度”重新开始。` })
  } finally {
    loading.value = false
  }
}

// 场景参数也通过对话触发后端提问，不再使用独立的页面表单。
async function requestSceneCard() {
  if (loading.value || pendingCard.value) return
  input.value = '请生成生产调度与计算卸载方案'
  await send()
}

async function copyMessage(message) {
  try {
    await navigator.clipboard.writeText(message.content)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}
</script>

<template>
  <div class="schedule-page">
    <AppNav>
      <template #actions>
        <el-button text bg size="small" :disabled="loading || !!pendingCard" @click="requestSceneCard"><el-icon style="margin-right: 4px"><Setting /></el-icon>场景参数</el-button>
      </template>
    </AppNav>

    <div class="agent-workspace">
      <AgentContextPanel mode="scheduling" />
      <div class="agent-conversation">
    <main ref="flowEl" class="flow">
      <div class="flow-inner">
        <template v-for="(message, index) in messages" :key="index">
          <div v-if="message.type === 'user'" class="row user"><div class="bubble">{{ message.content }}</div></div>
          <div v-else-if="message.type === 'assistant'" class="row bot">
            <div class="bot-label"><el-icon><ChatDotRound /></el-icon>调度智能体</div>
            <div class="bot-text md-content" v-html="renderMarkdown(message.content)"></div>
            <div class="msg-toolbar">
              <el-tooltip content="复制" placement="top" :show-after="300"><el-button text class="tool-btn" @click="copyMessage(message)"><el-icon><CopyDocument /></el-icon></el-button></el-tooltip>
            </div>
          </div>
          <div v-else-if="message.type === 'card'" class="row bot">
            <div class="ask-card">
              <div class="ask-head"><el-icon><ChatDotRound /></el-icon><b>{{ cardTitle(message.reason) }}</b></div>
              <div class="ask-question">{{ message.question }}</div>
              <template v-if="!message.answered">
                <div v-if="message.reason === 'missing_scheduling_parameters' || message.reason?.startsWith('invalid_')" class="card-example">
                  <span>填写示例（可按需修改）</span>
                  <code>18个工件、7台机器、3个边缘节点，随机种子41055</code>
                  <small>数量填写正整数；随机种子用于复现相同场景。也可以只补充上面缺少的参数。</small>
                </div>
                <div v-if="cardActions(message.reason).length" class="ask-actions">
                  <el-button v-for="action in cardActions(message.reason)" :key="action" size="small" round @click="chooseCardAction(message, action)">{{ action }}</el-button>
                </div>
                <div class="ask-input">
                  <el-input v-model="message.draft" :placeholder="cardPlaceholder(message.reason)" @keydown.enter.exact.prevent="submitAnswer(message)" />
                  <el-button type="primary" :disabled="!message.draft.trim()" @click="submitAnswer(message)">提交</el-button>
                </div>
              </template>
              <div v-else class="ask-done"><el-icon><Check /></el-icon>已选择：{{ message.answer }}</div>
            </div>
          </div>
          <div v-else class="row bot"><div class="error-card"><b>运行失败</b><span>{{ message.content }}</span></div></div>
        </template>

        <section v-if="result" class="result-block">
          <div class="result-head">
            <div class="result-status"><span></span><b>联合 MAPPO 计算完成</b><small>{{ result.elapsed_seconds }} 秒</small></div>
            <code>{{ result.run_id?.slice(0, 8) }}</code>
          </div>
          <div class="metrics-grid">
            <article><span>最大完工时间</span><b>{{ number(metrics.makespan) }}</b><small>越小越好</small></article>
            <article><span>总拖期</span><b>{{ number(metrics.total_tardiness) }}</b><small>交期表现</small></article>
            <article><span>机器利用率</span><b>{{ percent(metrics.machine_utilization) }}</b><small>设备负载</small></article>
            <article><span>平均质检阻塞</span><b>{{ number(metrics.mean_blocking) }}</b><small>等待检测结果</small></article>
            <article><span>质检超时率</span><b>{{ percent(metrics.inspection_timeout_rate) }}</b><small>计算及时性</small></article>
            <article><span>最大超时</span><b>{{ number(metrics.max_timeout_lateness) }}</b><small>尾部风险</small></article>
          </div>
          <div class="detail-panel">
            <el-tabs v-model="activeTab">
              <el-tab-pane label="决策过程" name="decisions">
                <el-table :data="decisions" height="330" stripe>
                  <el-table-column prop="time" label="仿真时刻" width="110" />
                  <el-table-column label="决策类型" width="125"><template #default="scope"><el-tag size="small" :type="kindType(scope.row.kind)" effect="light">{{ kindLabel(scope.row.kind) }}</el-tag></template></el-table-column>
                  <el-table-column prop="action" label="决策内容" min-width="260" />
                </el-table>
              </el-tab-pane>
              <el-tab-pane label="计算节点" name="compute"><el-table :data="computeChoices" stripe><el-table-column prop="node" label="计算位置" /><el-table-column prop="count" label="质检任务数" /></el-table></el-tab-pane>
              <el-tab-pane label="场景信息" name="scenario">
                <el-descriptions :column="2" border>
                  <el-descriptions-item label="随机种子">{{ result.scenario.seed }}</el-descriptions-item><el-descriptions-item label="工件数量">{{ result.scenario.number_of_jobs }}</el-descriptions-item>
                  <el-descriptions-item label="候选关系">工序直接指定机器</el-descriptions-item><el-descriptions-item label="机器数量">{{ result.scenario.machine_count }}</el-descriptions-item>
                  <el-descriptions-item label="边缘节点">{{ result.scenario.edge_count ?? 2 }}</el-descriptions-item><el-descriptions-item label="每工序候选数">{{ result.scenario.eligible_machines_per_operation }}</el-descriptions-item>
                  <el-descriptions-item label="柔性模式">{{ result.scenario.machine_flexibility }}</el-descriptions-item><el-descriptions-item label="决策总数">{{ result.decision_summary.total }}</el-descriptions-item>
                </el-descriptions>
              </el-tab-pane>
            </el-tabs>
          </div>
        </section>

        <div v-if="loading" class="row bot"><span class="dots"><i></i><i></i><i></i></span><small class="loading-text">正在计算方案并整理分析，完成后将一起展示…</small></div>

        <section v-if="!messages.length && !result && !loading" class="empty-hint">
          <div class="agent-icon"><el-icon><ChatDotRound /></el-icon></div>
          <h1>生产调度与计算卸载</h1>
          <p>描述你要安排的工件，智能体会选择加工机器，并为在线质检任务分配本地、边缘或云端算力。</p>
          <div class="sample-grid">
            <button @click="fillSample('使用默认场景生成一个联合调度与计算卸载方案。')"><b>快速生成方案</b><span>使用20个工件和默认参数</span></button>
            <button @click="fillSample('为20个工件运行联合调度，并解释质检超时情况。')"><b>分析质检超时</b><span>计算方案并解释关键指标</span></button>
            <button @click="fillSample('什么是生产调度与计算卸载的耦合？')"><b>了解决策原理</b><span>先解释问题，不运行算法</span></button>
          </div>
          <button class="config-link" @click="requestSceneCard">需要更换随机场景？打开场景参数</button>
        </section>
      </div>
    </main>

    <footer class="input-dock">
      <div class="composer-box">
        <el-input v-model="input" type="textarea" :rows="1" resize="none" :disabled="!!pendingCard" :placeholder="pendingCard ? '请先回应上方确认卡片' : '描述你的调度需求…'" class="composer-input" @keydown.enter.exact.prevent="send" />
        <div class="composer-foot"><span>{{ pendingCard ? '请先回应确认卡片' : 'Enter 发送，Shift + Enter 换行' }}</span><el-button type="primary" circle class="send-btn" :loading="loading" :disabled="!!pendingCard || !input.trim()" @click="send"><el-icon><Promotion /></el-icon></el-button></div>
      </div>
    </footer>
      </div>
      <ResultInsights mode="scheduling" :data="result" :messages="messages" :loading="loading" />
    </div>
  </div>
</template>

<style scoped>
.schedule-page { display:flex; flex-direction:column; height:100vh; overflow:hidden; background:var(--bg); color:var(--text); }
.flow{flex:1;min-height:0;overflow-y:auto;width:100%;padding:20px 16px 12px;scrollbar-width:thin;scrollbar-color:#d3d7de transparent}.flow::-webkit-scrollbar{width:6px}.flow::-webkit-scrollbar-thumb{background:#d3d7de;border-radius:3px}.flow-inner{width:100%;max-width:820px;margin:0 auto;display:flex;flex-direction:column}
.row{display:flex;flex-direction:column}.row+.row,.row+.result-block,.result-block+.row{margin-top:18px}.row.user{align-items:flex-end;padding-right:8px}.row.user:not(:first-child){margin-top:24px}.bubble{max-width:72%;padding:11px 16px;border-radius:20px;background:var(--primary-light);line-height:1.65;white-space:pre-wrap;word-break:break-word}.row.bot{align-items:stretch}.bot-label{display:flex;align-items:center;gap:6px;margin-bottom:5px;color:var(--primary);font-size:12px;font-weight:600}.bot-text{width:100%;padding:3px 2px;font-size:14px;line-height:1.7;word-break:break-word}.msg-toolbar{margin-top:4px;opacity:0;transition:opacity .15s}.row.bot:hover .msg-toolbar{opacity:1}.tool-btn{color:var(--text-muted)}
.error-card{display:flex;flex-direction:column;gap:5px;padding:13px 15px;border:1px solid #fecaca;border-radius:12px;background:#fff8f8;color:#b91c1c}.error-card span{font-size:13px;line-height:1.6}
.ask-card{padding:15px 16px;border:1px solid #f3cf83;border-radius:13px;background:#fffbf2}.ask-head{display:flex;align-items:center;gap:8px;color:#9a6700}.ask-question{margin:11px 0;color:var(--text);font-size:13px;line-height:1.65}.ask-actions{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:10px}.ask-actions :deep(.el-button){margin-left:0}.card-example{display:flex;flex-direction:column;gap:7px;margin:12px 0;padding:11px 12px;border-radius:8px;background:rgba(255,255,255,.75)}.card-example span{font-size:12px;font-weight:600}.card-example code{font-family:inherit;font-size:13px;line-height:1.7;white-space:normal}.card-example small{color:var(--text-muted);font-size:12px;line-height:1.6}.ask-input{display:flex;gap:8px}.ask-done{display:flex;align-items:center;gap:6px;color:#498467;font-size:13px}
.empty-hint{margin:8vh auto 0;width:100%;max-width:650px;text-align:center}.agent-icon{display:grid;place-items:center;width:44px;height:44px;margin:0 auto 14px;border-radius:14px;background:var(--primary-light);color:var(--primary);font-size:22px}.empty-hint h1{margin:0;font-size:21px}.empty-hint>p{max-width:590px;margin:10px auto 22px;color:var(--text-muted);font-size:13px;line-height:1.7}.sample-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.sample-grid button{padding:14px;border:1px solid var(--border);border-radius:12px;background:var(--bg);color:var(--text);cursor:pointer;text-align:left;transition:border-color .15s,box-shadow .15s,transform .15s}.sample-grid button:hover{border-color:var(--primary);box-shadow:var(--shadow);transform:translateY(-1px)}.sample-grid b,.sample-grid span{display:block}.sample-grid b{margin-bottom:6px;font-size:13px}.sample-grid span{color:var(--text-muted);font-size:12px;line-height:1.5}.config-link{margin-top:16px;border:0;background:transparent;color:var(--primary);cursor:pointer;font-size:12px}
.config-card{margin-bottom:24px;padding:17px 18px;border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)}.card-heading{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px}.card-heading b,.card-heading span{display:block}.card-heading span{margin-top:4px;color:var(--text-muted);font-size:12px}.form-grid{display:grid;grid-template-columns:1fr 1fr auto;gap:14px;align-items:end}.form-grid :deep(.el-form-item){margin-bottom:0}.form-grid :deep(.el-form-item__label){color:#606875;font-size:12px}.form-grid :deep(.el-input-number){width:100%}.run-cell{padding-bottom:1px}.scene-line{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:14px;padding:9px 11px;border-radius:8px;background:var(--fill-light);color:#606875;font-size:12px}.scene-line i{width:3px;height:3px;border-radius:50%;background:#aab1bc}
.result-block{margin-top:18px}.result-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:11px}.result-status{display:flex;align-items:center;gap:8px}.result-status>span{width:8px;height:8px;border-radius:50%;background:#27b67a}.result-status small,.result-head code{color:var(--text-muted);font-size:12px}.warning{margin-bottom:10px}.metrics-grid{display:grid;grid-template-columns:repeat(3,1fr);overflow:hidden;border:1px solid var(--border);border-radius:12px}.metrics-grid article{padding:15px 17px;border-right:1px solid var(--border);border-bottom:1px solid var(--border)}.metrics-grid article:nth-child(3n){border-right:0}.metrics-grid article:nth-child(n+4){border-bottom:0}.metrics-grid span,.metrics-grid small{display:block;color:var(--text-muted);font-size:12px}.metrics-grid b{display:block;margin:7px 0 4px;font-size:20px;font-variant-numeric:tabular-nums}.detail-panel{margin-top:13px;padding:8px 16px 16px;border:1px solid var(--border);border-radius:12px}
.dots{display:inline-flex;align-items:center;gap:5px;height:18px}.dots i{width:6px;height:6px;border-radius:50%;background:var(--text-muted);animation:bounce 1.2s infinite ease-in-out}.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}.loading-text{margin-top:6px;color:var(--text-muted)}@keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:.4}30%{transform:translateY(-4px);opacity:1}}
.input-dock{width:100%;max-width:820px;margin:0 auto;padding:8px 16px 16px}.composer-box{display:flex;flex-direction:column;gap:8px;padding:12px 16px 10px;border:2px solid transparent;border-radius:16px;background:linear-gradient(var(--bg),var(--bg)) padding-box,linear-gradient(90deg,#ff8bd2,#b57bee,#5b8def) border-box;box-shadow:var(--shadow)}.composer-input :deep(.el-textarea__inner){min-height:26px;padding:2px 4px;border:0;box-shadow:none;background:transparent;font-family:inherit;font-size:14px;line-height:1.6}.composer-foot{display:flex;align-items:center;justify-content:space-between}.composer-foot>span{color:var(--text-placeholder);font-size:12px}.send-btn{width:32px;height:32px;padding:0}
.md-content :deep(p){margin:7px 0}.md-content :deep(p:first-child){margin-top:0}.md-content :deep(ul),.md-content :deep(ol){margin:8px 0;padding-left:22px}.md-content :deep(li){margin:4px 0}.md-content :deep(code){padding:1px 5px;border-radius:4px;background:var(--fill-light)}.md-content :deep(table){border-collapse:collapse;margin:10px 0;font-size:13px}.md-content :deep(th),.md-content :deep(td){padding:6px 10px;border:1px solid var(--border)}.md-content :deep(th){background:var(--fill-light)}
@media(max-width:760px){.flow{padding-inline:12px}.input-dock{padding-inline:12px}.sample-grid{grid-template-columns:1fr}.form-grid{grid-template-columns:1fr 1fr}.run-cell{grid-column:1/-1}.run-cell :deep(.el-button){width:100%}.metrics-grid{grid-template-columns:repeat(2,1fr)}.metrics-grid article,.metrics-grid article:nth-child(3n),.metrics-grid article:nth-child(n+4){border-right:1px solid var(--border);border-bottom:1px solid var(--border)}.metrics-grid article:nth-child(2n){border-right:0}.metrics-grid article:nth-last-child(-n+2){border-bottom:0}.bubble{max-width:88%}}
</style>
