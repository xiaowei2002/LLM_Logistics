<script setup>
import { computed } from 'vue'
import { Connection as ConnectionIcon, DataAnalysis, Histogram, PieChart, Timer, TrendCharts } from '@element-plus/icons-vue'
import BusinessChart from './BusinessChart.vue'
const props = defineProps({ mode: { type: String, default: 'general' }, data: { type: Object, default: null }, messages: { type: Array, default: () => [] }, loading: Boolean })
const palette = ['#348cf3', '#23b6bc', '#7d96b4', '#90bde8']
const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
const format = value => finite(value) ? Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '—'
const forecast = computed(() => props.data?.state_conditioned_result || props.data?.result || props.data)
const periods = computed(() => Object.entries(forecast.value?.forecast || {}).sort(([a], [b]) => Number(a.replace('t+', '')) - Number(b.replace('t+', ''))).map(([key, value]) => ({ ...value, horizon: value.horizon || key })))
const materials = computed(() => [...new Set(periods.value.flatMap(p => Object.keys(p.material_demand || {})))].sort())
const hasForecast = computed(() => materials.value.length > 0)
const peakRows = computed(() => materials.value.map(name => ({ name, value: Math.max(...periods.value.map(p => p.material_demand?.[name]).filter(finite).map(Number)) })).filter(row => Number.isFinite(row.value)).sort((a, b) => b.value - a.value))
const forecastOption = computed(() => ({ color: palette, tooltip: { trigger: 'axis', confine: true }, legend: { top: 4, icon: 'roundRect', itemWidth: 12, itemHeight: 5, textStyle: { color: '#50677e', fontSize: 11 } }, grid: { left: 40, right: 12, top: 40, bottom: 34 }, xAxis: { type: 'category', data: materials.value, axisTick: { show: false }, axisLine: { lineStyle: { color: '#d8e2e9' } }, axisLabel: { color: '#50677e', fontSize: 10, interval: 0, rotate: materials.value.length > 6 ? 35 : 0 } }, yAxis: { type: 'value', axisLabel: { color: '#50677e', fontSize: 10 }, splitLine: { lineStyle: { color: '#d4dde7' } } }, series: periods.value.map(p => ({ name: p.horizon, type: 'bar', barMaxWidth: 14, itemStyle: { borderRadius: [3, 3, 0, 0] }, data: materials.value.map(m => finite(p.material_demand?.[m]) ? Number(p.material_demand[m]) : null) })) }))
const metrics = computed(() => props.data?.metrics || {})
const computeRows = computed(() => {
  const counts = new Map()
  Object.entries(metrics.value.compute_choices || {}).forEach(([name, value]) => {
    if (!finite(value) || Number(value) < 0) return
    const label = name.startsWith('LOCAL') ? '本地' : name === 'CLOUD' ? '云端' : name.replace('EDGE', '边缘 ')
    counts.set(label, (counts.get(label) || 0) + Number(value))
  })
  return [...counts].map(([name, value]) => ({ name, value })).filter(row => row.value > 0)
})
const computeTotal = computed(() => computeRows.value.reduce((sum, row) => sum + row.value, 0))
const computeOption = computed(() => ({ color: palette, tooltip: { trigger: 'item', confine: true }, legend: { show: false }, series: [{ type: 'pie', radius: ['60%', '83%'], center: ['50%', '50%'], label: { show: false }, itemStyle: { borderColor: '#e9eef4', borderWidth: 3, borderRadius: 3 }, data: computeRows.value }], graphic: [{ type: 'text', left: 'center', top: '42%', style: { text: String(computeTotal.value), fill: '#193c50', fontSize: 26, fontWeight: 600 } }, { type: 'text', left: 'center', top: '59%', style: { text: '检测任务', fill: '#738897', fontSize: 11 } }] }))
const qualityRows = computed(() => [{ name: '机器利用率', value: metrics.value.machine_utilization }, { name: '检测按期率', value: metrics.value.deadline_satisfaction_rate }].filter(row => finite(row.value) && Number(row.value) >= 0 && Number(row.value) <= 1))
const schedulingStats = computed(() => [{ label: '完工时间', value: metrics.value.makespan, unit: '仿真时间' }, { label: '总拖期', value: metrics.value.total_tardiness, unit: '仿真时间' }])
const generalStats = computed(() => [{ label: '用户提问', value: props.messages.filter(m => m.role === 'user').length }, { label: '助手回答', value: props.messages.filter(m => m.role === 'assistant' && m.content && !m.streaming).length }])
const lastAnswer = computed(() => [...props.messages].reverse().find(m => (m.type === 'assistant' || m.role === 'assistant') && !m.streaming)?.content || '')
const headings = computed(() => lastAnswer.value.split('\n').filter(line => /^#{1,4}\s|^\*\*[^*]+\*\*\s*[:：]?/.test(line)).map(line => line.replace(/^#+\s*/, '').replace(/\*\*/g, '').trim()).filter(Boolean).slice(0, 5))
const answerPreview = computed(() => lastAnswer.value.replace(/[#*`>|]/g, '').trim().slice(0, 160))
</script>

<template>
  <aside class="insights-panel" :class="{ 'soft-analysis': mode !== 'general' }" aria-label="结果分析">
    <header class="insights-head"><div><span class="insights-kicker">结果工作台</span><h2><el-icon><DataAnalysis /></el-icon>{{ mode === 'general' ? '对话概览' : '可视化分析' }}</h2></div><span class="analysis-status" :class="{ active: data || lastAnswer }">{{ loading ? '处理中' : data || lastAnswer ? '已更新' : '待生成' }}</span></header>
    <template v-if="mode === 'forecast' && hasForecast">
      <div class="insight-stats"><div><small>覆盖物料</small><b>{{ materials.length }}<em>种</em></b></div><div><small>预测时段</small><b>{{ periods.length }}<em>个</em></b></div></div>
      <section class="insight-card"><div class="chart-heading"><el-icon><Histogram /></el-icon><h3>物料需求对比</h3></div><p>按物料比较各预测时段的需求数量</p><BusinessChart :option="forecastOption" label="各物料在不同预测时段的需求柱状图" :height="250"/><div class="period-note" v-for="period in periods" :key="period.horizon"><b>{{ period.horizon }}</b><span>{{ period.timestamp || '算法返回的预测时段' }}</span></div></section>
      <section class="insight-card"><div class="chart-heading"><el-icon><TrendCharts /></el-icon><h3>单时段峰值排序</h3></div><p>取预测时段中的最大值，不是两小时累计量</p><div class="rank-row" v-for="(row, index) in peakRows.slice(0, 5)" :key="row.name"><span class="rank-number">{{ String(index + 1).padStart(2, '0') }}</span><div><b>{{ row.name }}</b><div class="rank-track"><i :style="{ width: `${peakRows[0]?.value > 0 ? row.value / peakRows[0].value * 100 : 0}%` }"></i></div></div><strong>{{ format(row.value) }}</strong></div></section>
      <div class="source-note">数据来自预测工具 · 基准时刻 {{ forecast.forecast_timestamp || '—' }}</div>
    </template>
    <template v-else-if="mode === 'scheduling' && data">
      <div class="insight-stats"><div v-for="stat in schedulingStats" :key="stat.label"><small>{{ stat.label }}</small><b>{{ format(stat.value) }}</b><span>{{ stat.unit }}</span></div></div>
      <section class="insight-card" v-if="computeRows.length"><div class="chart-heading"><el-icon><PieChart /></el-icon><h3>检测计算分布</h3></div><p>本地、边缘与云端实际承担的任务数</p><BusinessChart :option="computeOption" label="检测任务在各计算位置的分布环形图" :height="205"/><div class="node-legend" v-for="(row, index) in computeRows" :key="row.name"><i :style="{ background: palette[index % palette.length] }"></i><span>{{ row.name }}</span><b>{{ row.value }}</b><small>{{ (row.value / computeTotal * 100).toFixed(1) }}%</small></div></section>
      <section class="insight-card"><div class="chart-heading"><el-icon><Timer /></el-icon><h3>生产与检测表现</h3></div><div class="quality-row" v-for="row in qualityRows" :key="row.name"><div><span>{{ row.name }}</span><b>{{ (Number(row.value) * 100).toFixed(1) }}%</b></div><div class="quality-track"><i :style="{ width: `${Number(row.value) * 100}%` }"></i></div></div><div class="timeout-line"><span>检测超时率</span><b>{{ finite(metrics.inspection_timeout_rate) ? `${(Number(metrics.inspection_timeout_rate) * 100).toFixed(1)}%` : '—' }}</b></div></section>
    </template>
    <template v-else-if="mode === 'general'">
      <div class="insight-stats"><div v-for="stat in generalStats" :key="stat.label"><small>{{ stat.label }}</small><b>{{ stat.value }}<em>条</em></b></div></div>
      <section class="insight-card"><div class="chart-heading"><el-icon><DataAnalysis /></el-icon><h3>最近回答要点</h3></div><template v-if="lastAnswer"><ul v-if="headings.length" class="answer-points"><li v-for="heading in headings" :key="heading">{{ heading }}</li></ul><p v-else class="answer-preview">{{ answerPreview }}{{ lastAnswer.length > 160 ? '…' : '' }}</p></template><p v-else class="empty-copy">开始对话后，在这里查看最近回答的内容概览。</p></section>
      <section class="insight-card"><div class="chart-heading"><el-icon><ConnectionIcon /></el-icon><h3>业务结果分析</h3></div><p class="empty-copy">进入需求预测或调度页面，生成方案后可查看对应的物料对比与算力分布图。</p><div class="workspace-links"><router-link to="/forecast">需求预测 <span>↗</span></router-link><router-link to="/scheduling-offloading">调度与计算卸载 <span>↗</span></router-link></div></section>
    </template>
    <template v-else>
      <section class="insight-card insight-empty"><div class="empty-chart" aria-hidden="true"><i></i><i></i><i></i><i></i><span></span></div><h3>{{ loading ? '正在准备分析结果' : '等待本次结果' }}</h3><p>{{ mode === 'forecast' ? '完成预测后，这里展示物料需求对比和峰值排序。' : '完成调度后，这里展示检测计算分布和生产表现。' }}</p><span class="empty-label">{{ mode === 'forecast' ? '需求对比 · 峰值排序' : '算力分布 · 生产指标' }}</span></section>
    </template>
  </aside>
</template>

<style scoped>
.insights-panel{min-width:0;overflow-y:auto;padding:24px 18px;background:#f7f9fb;border-left:1px solid #dce3eb;scrollbar-width:thin}.insights-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}.insights-kicker{font-size:10px;letter-spacing:1.5px;color:#879cab}.insights-head h2{display:flex;align-items:center;gap:7px;font-size:16px;margin:7px 0 0;font-weight:650}.insights-head h2 .el-icon{color:#308d94}.analysis-status{font-size:10px;padding:4px 7px;border-radius:4px;background:#e8eef3;color:#718795}.analysis-status.active{color:#287c78;background:#e6f2ef}.insight-stats{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}.insight-stats>div{padding:14px 12px;background:white;border:1px solid #dfe7ee;border-radius:8px;min-width:0}.insight-stats small{display:block;color:#718795;font-size:11px}.insight-stats b{display:block;font-size:23px;font-weight:650;margin-top:8px;overflow-wrap:anywhere}.insight-stats em{font-size:11px;font-style:normal;font-weight:400;margin-left:6px;color:#879cab}.insight-stats span{display:block;font-size:10px;color:#879cab;margin-top:4px}.insight-card{margin-bottom:14px;background:#fff;border:1px solid #dfe7ee;border-radius:8px;padding:16px 13px;box-shadow:0 3px 12px #153e5803}.chart-heading{display:flex;align-items:center;gap:7px}.chart-heading .el-icon{font-size:15px;color:#62889e}.chart-heading h3{font-size:13px;font-weight:650;margin:0}.insight-card>p{font-size:11px;line-height:1.8;color:#8796a3;margin:7px 0 4px}.period-note{display:flex;gap:8px;font-size:10px;padding-top:7px;color:#718795}.period-note b{color:#3b637b;font-weight:500}.rank-row{display:flex;align-items:center;gap:9px;margin-top:16px}.rank-number{color:#9aafbd;font-size:10px}.rank-row>div{flex:1}.rank-row b{font-size:11px;font-weight:500}.rank-row strong{font-size:12px;min-width:45px;text-align:right}.rank-track{height:4px;background:#edf2f5;border-radius:2px;margin-top:6px;overflow:hidden}.rank-track i{height:100%;display:block;background:#628ca4;border-radius:2px}.node-legend{display:flex;align-items:center;gap:8px;font-size:11px;padding:7px 0}.node-legend i{width:7px;height:7px;border-radius:2px}.node-legend span{flex:1;color:#607888}.node-legend b{font-weight:600}.node-legend small{width:44px;text-align:right;color:#8da0ad}.quality-row{margin-top:18px}.quality-row>div:first-child{display:flex;justify-content:space-between;font-size:11px;color:#607888;margin-bottom:8px}.quality-row b{color:#284f69}.quality-track{height:7px;border-radius:4px;background:#edf2f5;overflow:hidden}.quality-track i{display:block;height:100%;background:#39989c;border-radius:4px}.timeout-line{display:flex;justify-content:space-between;margin-top:20px;padding-top:12px;border-top:1px solid #edf2f5;font-size:11px;color:#718795}.timeout-line b{color:#284f69}.source-note{font-size:10px;line-height:1.8;color:#8b9daa;padding:0 3px}.insight-empty{text-align:center;padding:24px 18px}.empty-chart{height:76px;display:flex;align-items:flex-end;justify-content:center;gap:7px;margin:8px auto 22px;position:relative;width:132px;border-bottom:1px solid #cad9e4;padding:0 12px}.empty-chart i{width:17px;background:#d7e4ed;border-radius:3px 3px 0 0;height:34px}.empty-chart i:nth-child(2){height:58px;background:#afc8d9}.empty-chart i:nth-child(3){height:43px;background:#c3d9e3}.empty-chart i:nth-child(4){height:68px;background:#90b5c6}.insight-empty h3{font-size:14px;font-weight:600}.insight-empty>p{margin:10px 0 18px}.empty-label{display:inline-block;font-size:10px;padding:6px 12px;background:#edf3f7;border-radius:20px;color:#7895a7}.empty-copy{font-size:12px!important;line-height:1.9!important;margin-top:12px!important}.answer-preview{font-size:12px!important;color:#607888!important;line-height:1.9!important}.answer-points{padding-left:16px;margin-top:12px;color:#607888;font-size:12px;line-height:1.8}.answer-points li{margin-bottom:7px}.workspace-links{display:flex;flex-direction:column;gap:8px;margin-top:14px}.workspace-links a{display:flex;justify-content:space-between;color:#284f69;text-decoration:none;font-size:12px;padding:10px;background:#f0f5f8;border-radius:5px}.workspace-links a:hover{background:#e4eef4}

/* 新拟物效果仅用于两个业务智能体的分析栏，保留现有布局和数据。 */
.soft-analysis {
  --analysis-surface: #e9eef4;
  --analysis-ink: #19364d;
  --analysis-muted: #52697e;
  --analysis-raised: 5px 5px 12px #cbd5e180, -5px -5px 12px #ffffffd9;
  background: #e7ecf2;
  border-left: 1px solid #dbe3ec;
  padding: 24px 18px;
  color: var(--analysis-ink);
  scrollbar-color: #bac9d8 transparent;
  scrollbar-gutter: stable;
}
.soft-analysis .insights-head { margin-bottom: 22px; gap: 8px; }
.soft-analysis .insights-kicker { color: var(--analysis-muted); letter-spacing: 1px; }
.soft-analysis .insights-head h2 { font-size: 18px; color: var(--analysis-ink); margin-top: 6px; }
.soft-analysis .insights-head h2 .el-icon { color: #348cf3; }
.soft-analysis .analysis-status {
  flex-shrink: 0;
  border-radius: 12px;
  background: var(--analysis-surface);
  color: var(--analysis-muted);
  box-shadow: inset 2px 2px 4px #cbd5e180, inset -2px -2px 4px #ffffffd9;
  padding: 5px 9px;
}
.soft-analysis .analysis-status.active { color: #157c83; }
.soft-analysis .insight-stats { gap: 12px; margin-bottom: 20px; }
.soft-analysis .insight-stats > div,
.soft-analysis .insight-card {
  background: var(--analysis-surface);
  border: 1px solid #ffffff70;
  border-radius: 12px;
  box-shadow: var(--analysis-raised);
}
.soft-analysis .insight-stats > div { padding: 16px 12px; }
.soft-analysis .insight-stats small { color: var(--analysis-muted); font-size: 11px; line-height: 1.6; }
.soft-analysis .insight-stats b { color: var(--analysis-ink); font-size: clamp(21px, 1.65vw, 27px); line-height: 1.25; font-variant-numeric: tabular-nums; letter-spacing: -0.5px; }
.soft-analysis .insight-stats em,
.soft-analysis .insight-stats span { color: var(--analysis-muted); }
.soft-analysis .insight-card { padding: 18px 14px; margin-bottom: 20px; }
.soft-analysis .chart-heading { gap: 8px; }
.soft-analysis .chart-heading h3 { font-size: 14px; color: var(--analysis-ink); line-height: 1.5; }
.soft-analysis .chart-heading .el-icon { color: #4d7398; }
.soft-analysis .insight-card > p { color: var(--analysis-muted); margin-top: 9px; }
.soft-analysis .period-note { color: var(--analysis-muted); flex-wrap: wrap; row-gap: 3px; }
.soft-analysis .period-note b { color: #2869a7; }
.soft-analysis .rank-row { gap: 8px; margin-top: 18px; }
.soft-analysis .rank-row > div { min-width: 0; }
.soft-analysis .rank-row b { overflow-wrap: anywhere; color: var(--analysis-ink); }
.soft-analysis .rank-number { color: var(--analysis-muted); }
.soft-analysis .rank-row strong { color: #2869a7; font-variant-numeric: tabular-nums; }
.soft-analysis .rank-track,
.soft-analysis .quality-track {
  height: 7px;
  background: #dbe4ee;
  border-radius: 8px;
  box-shadow: inset 2px 2px 3px #b9c8d76b, inset -2px -2px 3px #ffffffb3;
}
.soft-analysis .rank-track i { background: #348cf3; border-radius: 8px; }
.soft-analysis .quality-track i { background: #23b6bc; border-radius: 8px; }
.soft-analysis .node-legend { padding: 8px 0; gap: 7px; border-bottom: 1px solid #d5dfe9; }
.soft-analysis .node-legend:last-child { border-bottom: 0; }
.soft-analysis .node-legend i { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.soft-analysis .node-legend span,
.soft-analysis .node-legend small { color: var(--analysis-muted); }
.soft-analysis .node-legend b { font-variant-numeric: tabular-nums; color: var(--analysis-ink); }
.soft-analysis .quality-row > div:first-child { color: var(--analysis-muted); }
.soft-analysis .quality-row b { color: var(--analysis-ink); font-variant-numeric: tabular-nums; }
.soft-analysis .timeout-line { border-color: #d1dce7; color: var(--analysis-muted); }
.soft-analysis .timeout-line b { color: var(--analysis-ink); }
.soft-analysis .source-note { color: var(--analysis-muted); line-height: 1.9; }
.soft-analysis .insight-empty { padding: 26px 16px; }
.soft-analysis .empty-chart { border-bottom-color: #b7c9da; }
.soft-analysis .empty-chart i { background: #aec9e6; }
.soft-analysis .empty-chart i:nth-child(2) { background: #7caee1; }
.soft-analysis .empty-chart i:nth-child(3) { background: #9fc7cf; }
.soft-analysis .empty-chart i:nth-child(4) { background: #6594ba; }
.soft-analysis .empty-label { color: #466782; background: #e2eaf2; box-shadow: inset 2px 2px 4px #cbd5e180, inset -2px -2px 4px #ffffffb3; }
@media (prefers-contrast: more) {
  .soft-analysis .insight-card, .soft-analysis .insight-stats > div { border-color: #879db2; }
}

</style>
