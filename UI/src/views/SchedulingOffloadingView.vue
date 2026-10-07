<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, ChatDotRound, CopyDocument, Promotion, RefreshLeft, Setting, VideoPlay } from '@element-plus/icons-vue'

import { chatScheduling, optimizeScheduling } from '@/services/schedulingOffloading'
import { renderMarkdown } from '@/utils/markdown'

const router = useRouter()
const flowEl = ref(null)
const loading = ref(false)
const result = ref(null)
const activeTab = ref('decisions')
const input = ref('')
const messages = ref([])
const showConfig = ref(false)
const form = ref({
  engine: 'research', algorithm: 'midterm', seed: 41055, number_of_jobs: 20,
  population_size: 4, generations: 1, decision_horizon: 4, time_horizon: 20,
})

const metrics = computed(() => result.value?.metrics || {})
const decisions = computed(() => result.value?.decisions || [])
const computeChoices = computed(() => Object.entries(metrics.value.compute_choices || {}).map(([node, count]) => ({ node, count })))

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
  if (!query || loading.value) return
  messages.value.push({ type: 'user', content: query })
  input.value = ''
  loading.value = true
  try {
    const response = await chatScheduling(query)
    messages.value.push({ type: 'assistant', content: response.answer })
    if (response.run_result) result.value = response.run_result
  } catch (error) {
    messages.value.push({ type: 'error', content: error.message })
  } finally {
    loading.value = false
  }
}

async function runConfiguredScene() {
  if (loading.value) return
  loading.value = true
  messages.value.push({ type: 'user', content: `请按当前场景参数运行：${form.value.number_of_jobs}个工件，随机种子${form.value.seed}。` })
  try {
    result.value = await optimizeScheduling(form.value)
    messages.value.push({ type: 'assistant', content: `方案已生成。共完成 **${result.value?.decision_summary?.total ?? 0}** 次决策，结果如下。` })
    showConfig.value = false
    ElMessage.success('联合调度方案已生成')
  } catch (error) {
    messages.value.push({ type: 'error', content: error.message })
  } finally {
    loading.value = false
  }
}

async function copyMessage(message) {
  try {
    await navigator.clipboard.writeText(message.content)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

function reset() {
  if (loading.value) return
  messages.value = []
  result.value = null
  input.value = ''
  activeTab.value = 'decisions'
  showConfig.value = false
}
</script>

<template>
  <div class="schedule-page">
    <header class="top-head">
      <div class="bar-left">
        <el-button text bg size="small" @click="router.push('/')"><el-icon><Back /></el-icon>返回对话</el-button>
        <el-button text bg size="small" :disabled="(!messages.length && !result) || loading" @click="reset"><el-icon><RefreshLeft /></el-icon>新调度</el-button>
      </div>
      <div class="head-title">生产调度与计算卸载智能体</div>
      <div class="bar-right">
        <el-button text bg size="small" :disabled="loading" @click="showConfig = !showConfig"><el-icon><Setting /></el-icon>场景参数</el-button>
      </div>
    </header>

    <main ref="flowEl" class="flow">
      <div class="flow-inner">
        <section v-if="showConfig" class="config-card">
          <div class="card-heading">
            <div><b>场景参数</b><span>设置本次仿真的工件规模和随机场景</span></div>
            <el-tag size="small" effect="plain">仿真数据</el-tag>
          </div>
          <div class="form-grid">
            <el-form-item label="工件数量"><el-input-number v-model="form.number_of_jobs" :min="1" :max="100" /></el-form-item>
            <el-form-item label="随机种子"><el-input-number v-model="form.seed" :min="0" :controls="false" /></el-form-item>
            <div class="run-cell"><el-button type="primary" :icon="VideoPlay" :loading="loading" @click="runConfiguredScene">生成调度方案</el-button></div>
          </div>
          <div class="scene-line">
            <span>3个工作中心</span><i></i><span>6台机器</span><i></i><span>每道工序3台候选机器</span><i></i><span>本地、双边缘、云端</span>
          </div>
        </section>

        <template v-for="(message, index) in messages" :key="index">
          <div v-if="message.type === 'user'" class="row user"><div class="bubble">{{ message.content }}</div></div>
          <div v-else-if="message.type === 'assistant'" class="row bot">
            <div class="bot-label"><el-icon><ChatDotRound /></el-icon>调度智能体</div>
            <div class="bot-text md-content" v-html="renderMarkdown(message.content)"></div>
            <div class="msg-toolbar">
              <el-tooltip content="复制" placement="top" :show-after="300"><el-button text class="tool-btn" @click="copyMessage(message)"><el-icon><CopyDocument /></el-icon></el-button></el-tooltip>
            </div>
          </div>
          <div v-else class="row bot"><div class="error-card"><b>运行失败</b><span>{{ message.content }}</span></div></div>
        </template>

        <section v-if="result" class="result-block">
          <div class="result-head">
            <div class="result-status"><span></span><b>方案计算完成</b><small>{{ result.elapsed_seconds }} 秒</small></div>
            <code>{{ result.run_id?.slice(0, 8) }}</code>
          </div>
          <el-alert v-for="warning in result.warnings" :key="warning" class="warning" :title="warning" type="warning" show-icon :closable="false" />
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
                  <el-descriptions-item label="工作中心">{{ result.scenario.number_of_work_centers }}</el-descriptions-item><el-descriptions-item label="机器数量">{{ result.scenario.machine_count }}</el-descriptions-item>
                  <el-descriptions-item label="柔性模式">{{ result.scenario.machine_flexibility }}</el-descriptions-item><el-descriptions-item label="决策总数">{{ result.decision_summary.total }}</el-descriptions-item>
                </el-descriptions>
              </el-tab-pane>
            </el-tabs>
          </div>
        </section>

        <div v-if="loading" class="row bot"><span class="dots"><i></i><i></i><i></i></span><small class="loading-text">正在理解请求并计算调度方案…</small></div>

        <section v-if="!messages.length && !result && !loading" class="empty-hint">
          <div class="agent-icon"><el-icon><ChatDotRound /></el-icon></div>
          <h1>生产调度与计算卸载</h1>
          <p>描述你要安排的工件，智能体会选择加工机器，并为在线质检任务分配本地、边缘或云端算力。</p>
          <div class="sample-grid">
            <button @click="fillSample('使用默认场景生成一个联合调度与计算卸载方案。')"><b>快速生成方案</b><span>使用20个工件和默认参数</span></button>
            <button @click="fillSample('为20个工件运行联合调度，并解释质检超时情况。')"><b>分析质检超时</b><span>计算方案并解释关键指标</span></button>
            <button @click="fillSample('什么是生产调度与计算卸载的耦合？')"><b>了解决策原理</b><span>先解释问题，不运行算法</span></button>
          </div>
          <button class="config-link" @click="showConfig = true">需要指定工件数量或随机种子？打开场景参数</button>
        </section>
      </div>
    </main>

    <footer class="input-dock">
      <div class="composer-box">
        <el-input v-model="input" type="textarea" :rows="1" resize="none" placeholder="描述你的调度需求…" class="composer-input" @keydown.enter.exact.prevent="send" />
        <div class="composer-foot"><span>Enter 发送，Shift + Enter 换行</span><el-button type="primary" circle class="send-btn" :loading="loading" :disabled="!input.trim()" @click="send"><el-icon><Promotion /></el-icon></el-button></div>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.schedule-page { display:flex; flex-direction:column; height:100vh; overflow:hidden; background:var(--bg); color:var(--text); }
.top-head { position:relative; display:flex; align-items:center; gap:8px; padding:14px 20px 6px; }.bar-left{display:flex;gap:4px}.bar-right{margin-left:auto}.bar-left :deep(.el-button),.bar-right :deep(.el-button){gap:4px}.head-title{position:absolute;left:50%;transform:translateX(-50%);font-size:13.5px;font-weight:600}
.flow{flex:1;min-height:0;overflow-y:auto;width:100%;padding:20px 16px 12px;scrollbar-width:thin;scrollbar-color:#d3d7de transparent}.flow::-webkit-scrollbar{width:6px}.flow::-webkit-scrollbar-thumb{background:#d3d7de;border-radius:3px}.flow-inner{width:100%;max-width:820px;margin:0 auto;display:flex;flex-direction:column}
.row{display:flex;flex-direction:column}.row+.row,.row+.result-block,.result-block+.row{margin-top:18px}.row.user{align-items:flex-end;padding-right:8px}.row.user:not(:first-child){margin-top:24px}.bubble{max-width:72%;padding:11px 16px;border-radius:20px;background:var(--primary-light);line-height:1.65;white-space:pre-wrap;word-break:break-word}.row.bot{align-items:stretch}.bot-label{display:flex;align-items:center;gap:6px;margin-bottom:5px;color:var(--primary);font-size:12px;font-weight:600}.bot-text{width:100%;padding:3px 2px;font-size:14px;line-height:1.7;word-break:break-word}.msg-toolbar{margin-top:4px;opacity:0;transition:opacity .15s}.row.bot:hover .msg-toolbar{opacity:1}.tool-btn{color:var(--text-muted)}
.error-card{display:flex;flex-direction:column;gap:5px;padding:13px 15px;border:1px solid #fecaca;border-radius:12px;background:#fff8f8;color:#b91c1c}.error-card span{font-size:13px;line-height:1.6}
.empty-hint{margin:8vh auto 0;width:100%;max-width:650px;text-align:center}.agent-icon{display:grid;place-items:center;width:44px;height:44px;margin:0 auto 14px;border-radius:14px;background:var(--primary-light);color:var(--primary);font-size:22px}.empty-hint h1{margin:0;font-size:21px}.empty-hint>p{max-width:590px;margin:10px auto 22px;color:var(--text-muted);font-size:13px;line-height:1.7}.sample-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.sample-grid button{padding:14px;border:1px solid var(--border);border-radius:12px;background:var(--bg);color:var(--text);cursor:pointer;text-align:left;transition:border-color .15s,box-shadow .15s,transform .15s}.sample-grid button:hover{border-color:var(--primary);box-shadow:var(--shadow);transform:translateY(-1px)}.sample-grid b,.sample-grid span{display:block}.sample-grid b{margin-bottom:6px;font-size:13px}.sample-grid span{color:var(--text-muted);font-size:12px;line-height:1.5}.config-link{margin-top:16px;border:0;background:transparent;color:var(--primary);cursor:pointer;font-size:12px}
.config-card{margin-bottom:24px;padding:17px 18px;border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)}.card-heading{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px}.card-heading b,.card-heading span{display:block}.card-heading span{margin-top:4px;color:var(--text-muted);font-size:12px}.form-grid{display:grid;grid-template-columns:1fr 1fr auto;gap:14px;align-items:end}.form-grid :deep(.el-form-item){margin-bottom:0}.form-grid :deep(.el-form-item__label){color:#606875;font-size:12px}.form-grid :deep(.el-input-number){width:100%}.run-cell{padding-bottom:1px}.scene-line{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:14px;padding:9px 11px;border-radius:8px;background:var(--fill-light);color:#606875;font-size:12px}.scene-line i{width:3px;height:3px;border-radius:50%;background:#aab1bc}
.result-block{margin-top:18px}.result-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:11px}.result-status{display:flex;align-items:center;gap:8px}.result-status>span{width:8px;height:8px;border-radius:50%;background:#27b67a}.result-status small,.result-head code{color:var(--text-muted);font-size:12px}.warning{margin-bottom:10px}.metrics-grid{display:grid;grid-template-columns:repeat(3,1fr);overflow:hidden;border:1px solid var(--border);border-radius:12px}.metrics-grid article{padding:15px 17px;border-right:1px solid var(--border);border-bottom:1px solid var(--border)}.metrics-grid article:nth-child(3n){border-right:0}.metrics-grid article:nth-child(n+4){border-bottom:0}.metrics-grid span,.metrics-grid small{display:block;color:var(--text-muted);font-size:12px}.metrics-grid b{display:block;margin:7px 0 4px;font-size:20px;font-variant-numeric:tabular-nums}.detail-panel{margin-top:13px;padding:8px 16px 16px;border:1px solid var(--border);border-radius:12px}
.dots{display:inline-flex;align-items:center;gap:5px;height:18px}.dots i{width:6px;height:6px;border-radius:50%;background:var(--text-muted);animation:bounce 1.2s infinite ease-in-out}.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}.loading-text{margin-top:6px;color:var(--text-muted)}@keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:.4}30%{transform:translateY(-4px);opacity:1}}
.input-dock{width:100%;max-width:820px;margin:0 auto;padding:8px 16px 16px}.composer-box{display:flex;flex-direction:column;gap:8px;padding:12px 16px 10px;border:2px solid transparent;border-radius:16px;background:linear-gradient(var(--bg),var(--bg)) padding-box,linear-gradient(90deg,#ff8bd2,#b57bee,#5b8def) border-box;box-shadow:var(--shadow)}.composer-input :deep(.el-textarea__inner){min-height:26px;padding:2px 4px;border:0;box-shadow:none;background:transparent;font-family:inherit;font-size:14px;line-height:1.6}.composer-foot{display:flex;align-items:center;justify-content:space-between}.composer-foot>span{color:var(--text-placeholder);font-size:12px}.send-btn{width:32px;height:32px;padding:0}
.md-content :deep(p){margin:7px 0}.md-content :deep(p:first-child){margin-top:0}.md-content :deep(ul),.md-content :deep(ol){margin:8px 0;padding-left:22px}.md-content :deep(li){margin:4px 0}.md-content :deep(code){padding:1px 5px;border-radius:4px;background:var(--fill-light)}.md-content :deep(table){border-collapse:collapse;margin:10px 0;font-size:13px}.md-content :deep(th),.md-content :deep(td){padding:6px 10px;border:1px solid var(--border)}.md-content :deep(th){background:var(--fill-light)}
@media(max-width:760px){.head-title{display:none}.flow{padding-inline:12px}.input-dock{padding-inline:12px}.sample-grid{grid-template-columns:1fr}.form-grid{grid-template-columns:1fr 1fr}.run-cell{grid-column:1/-1}.run-cell :deep(.el-button){width:100%}.metrics-grid{grid-template-columns:repeat(2,1fr)}.metrics-grid article,.metrics-grid article:nth-child(3n),.metrics-grid article:nth-child(n+4){border-right:1px solid var(--border);border-bottom:1px solid var(--border)}.metrics-grid article:nth-child(2n){border-right:0}.metrics-grid article:nth-last-child(-n+2){border-bottom:0}.bubble{max-width:88%}}
</style>
