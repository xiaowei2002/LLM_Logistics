<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Expand, Fold, RefreshLeft, Search, Back } from '@element-plus/icons-vue'
import * as echarts from 'echarts'

import { fetchOverview, fetchStats, searchGraph } from '@/services/graph'

const router = useRouter()
const chartRef = ref(null)
const keyword = ref('')
const loading = ref(false)
const searching = ref(false)
const sideOpen = ref(true)

/* 侧边栏基本信息 */
const totals = ref({ nodes: 0, links: 0 })
const topRelTypes = ref([])
const matchedList = ref([])
const selected = ref(null) // { name, degree, rels: [{other, type, out}] }
/* 当前渲染的节点/边，必须用 ref 才能驱动侧栏的 computed 更新 */
const currentNodes = ref([])
const currentLinks = ref([])
/* 视图历史：进入新查询前把当前画面压栈，供"返回上一级"逐级回退 */
const viewHistory = ref([])
/* 当前视图是否由"点击展开"进入（区别于手动输入查询）：决定 Esc 是否清空 */
const expandedView = ref(false)

const topEntities = computed(() => currentNodes.value.slice(0, 10))

const selectedRels = computed(() => {
  if (!selected.value) return []
  const name = selected.value.name
  return currentLinks.value
    .filter((l) => l.source === name || l.target === name)
    .slice(0, 30)
    .map((l) => ({
      other: l.source === name ? l.target : l.source,
      type: l.relType,
      out: l.source === name,
    }))
})

let chart = null
/* 点击聚焦所需状态 */
let baseNodes = [] // 当前渲染的节点数据
let baseLinks = []
let focusedName = null
let currentMatched = new Set()

onMounted(async () => {
  chart = echarts.init(chartRef.value)
  window.addEventListener('resize', handleResize)
  // 点击空白处取消聚焦（zr 级监听只挂一次，注意不要对 zr 调用 off('click')，
  // 否则会把 ECharts 内部的点击转发器一并移除，导致所有点击失效）
  chart.getZr().on('click', (e) => {
    if (!e.target && focusedName) clearFocus()
  })
  await Promise.all([loadOverview(), loadStats()])
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

/* 侧栏收起/展开后画布尺寸会变，手动重算 */
watch(sideOpen, () => nextTick(() => chart?.resize()))

function handleResize() {
  chart?.resize()
}

async function loadStats() {
  try {
    const data = await fetchStats()
    topRelTypes.value = data.topRelTypes ?? []
  } catch {
    topRelTypes.value = []
  }
}

/* 全图概览：关联最强的 150 个节点 */
async function loadOverview() {
  loading.value = true
  try {
    const data = await fetchOverview()
    totals.value = { nodes: data.totalNodes, links: data.totalLinks }
    currentNodes.value = data.nodes
    currentLinks.value = data.links
    matchedList.value = []
    selected.value = null
    searching.value = false
    viewHistory.value = []
    expandedView.value = false
    render(new Set())
  } catch (e) {
    ElMessage.error('图谱加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

/* 关键词查询：匹配实体 + 一跳邻居子图 */
async function handleSearch(q = keyword.value, fromExpand = false) {
  const kw = (q ?? keyword.value).trim()
  if (!kw || loading.value) return
  /* 展开实体/新查询会覆盖画面，先把当前视图留档以便回退 */
  if (currentNodes.value.length) {
    viewHistory.value.push({
      keyword: keyword.value,
      nodes: currentNodes.value,
      links: currentLinks.value,
      matched: matchedList.value,
      expand: expandedView.value,
    })
  }
  keyword.value = kw
  loading.value = true
  try {
    const data = await searchGraph(kw)
    if (!data.nodes.length) {
      ElMessage.info(`没有找到与「${kw}」相关的实体`)
      return
    }
    currentNodes.value = data.nodes
    currentLinks.value = data.links
    matchedList.value = data.matched
    selected.value = null
    searching.value = true
    expandedView.value = fromExpand
    render(new Set(data.matched))
  } catch (e) {
    ElMessage.error('查询失败：' + e.message)
  } finally {
    loading.value = false
  }
}

function clearSearch() {
  keyword.value = ''
  loadOverview()
}

/* Esc 清空返回全图：仅手动输入查询进入的视图响应；点击展开进入的视图忽略 */
function handleEsc() {
  if (!expandedView.value) clearSearch()
}

/* 返回上一级视图：弹出最近一次留档的画面，不重新发请求 */
function goBack() {
  if (!viewHistory.value.length || loading.value) return
  const prev = viewHistory.value.pop()
  keyword.value = prev.keyword
  currentNodes.value = prev.nodes
  currentLinks.value = prev.links
  matchedList.value = prev.matched
  selected.value = null
  searching.value = true
  expandedView.value = prev.expand
  render(new Set(prev.matched))
}

/* 侧栏 Top 实体 / 搜索结果点击 = 展开该实体的关联子图 */
function expandEntity(name) {
  keyword.value = name
  handleSearch(name, true)
}

/* 点击图中节点 = 侧栏显示详情（不发请求） */
function selectNode(name) {
  const node = currentNodes.value.find((n) => n.name === name)
  if (node) selected.value = { name: node.name, degree: node.degree }
}

/* 计算每个节点/连线的完整样式。focusName 为空 = 常态；否则聚焦其邻域，背景淡化到 0.15。
   样式显式写全，保证切换时旧值被完全覆盖。 */
function applyStyles(focusName) {
  const nodesArr = currentNodes.value
  const maxDegree = Math.max(...nodesArr.map((n) => n.degree), 1)

  const cluster = new Set()
  if (focusName) {
    cluster.add(focusName)
    for (const l of baseLinks) {
      if (l.source === focusName) cluster.add(l.target)
      if (l.target === focusName) cluster.add(l.source)
    }
  }

  baseNodes.forEach((n, i) => {
    const src = nodesArr[i]
    const ratio = src.degree / maxDegree
    const baseColor = currentMatched.has(src.name)
      ? '#f97316'
      : `hsl(221, 75%, ${74 - ratio * 32}%)`
    const baseLabel =
      nodesArr.length <= 60 ||
      currentMatched.has(src.name) ||
      src.degree >= Math.max(3, maxDegree * 0.2)

    if (focusName && !cluster.has(n.name)) {
      // 背景淡化（保留轮廓可辨）
      n.itemStyle = { opacity: 0.15, borderWidth: 0, shadowBlur: 0 }
      n.label = { show: false }
    } else if (n.name === focusName) {
      n.itemStyle = { color: baseColor, borderWidth: 2, borderColor: '#0f172a', shadowBlur: 0 }
      n.label = { show: true }
    } else {
      n.itemStyle = { color: baseColor, opacity: 1, borderWidth: 0, shadowBlur: 0 }
      n.label = { show: focusName ? true : baseLabel }
    }
  })

  baseLinks.forEach((l) => {
    const incident = focusName && (l.source === focusName || l.target === focusName)
    l.lineStyle = incident
      ? { opacity: 1, width: 2 }
      : { opacity: focusName ? 0.15 : 0.7, width: 1 }
  })
}

/* 点击节点：高亮自身与邻居，其余淡化；再点一次取消 */
function focusNode(name) {
  if (loading.value) return
  if (focusedName === name) {
    clearFocus()
    return
  }
  focusedName = name
  applyStyles(name)
  chart.setOption({ series: [{ data: baseNodes, links: baseLinks }] })
  selectNode(name)
}

/* 点空白处 / 再点同一节点：恢复全图 */
function clearFocus() {
  focusedName = null
  applyStyles(null)
  chart.setOption({ series: [{ data: baseNodes, links: baseLinks }] })
  selected.value = null
}

/* 后端数据转 ECharts 力导向图配置 */
function render(matched) {
  const nodesArr = currentNodes.value
  const maxDegree = Math.max(...nodesArr.map((n) => n.degree), 1)
  currentMatched = matched
  baseNodes = nodesArr.map((n) => {
    const ratio = n.degree / maxDegree
    return {
      name: n.name,
      value: n.degree,
      symbolSize: 10 + Math.sqrt(n.degree) * 5,
    }
  })
  baseLinks = currentLinks.value.map((l) => ({
    source: l.source,
    target: l.target,
    value: l.relType,
  }))
  focusedName = null
  applyStyles(null)

  chart.setOption(
    {
      tooltip: {
        formatter: (p) =>
          p.dataType === 'edge'
            ? `${p.data.source} <b>—[${p.data.value}]→</b> ${p.data.target}`
            : `<b>${p.name}</b><br/>关联度：${p.value}（点击聚焦关联）`,
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          data: baseNodes,
          links: baseLinks,
          force: { repulsion: 170, gravity: 0.08, edgeLength: [40, 120], layoutAnimation: true },
          label: { position: 'right', fontSize: 11, color: '#3a3f4b' },
          lineStyle: { color: '#c4cad4', width: 1, curveness: 0.15, opacity: 0.7 },
          // 悬停只做轻微光晕，不改变全图（聚焦由点击手动实现）
          emphasis: {
            focus: 'none',
            itemStyle: { shadowBlur: 10, shadowColor: 'rgba(37, 99, 235, 0.35)' },
          },
        },
      ],
    },
    true,
  )

  chart.off('click')
  chart.on('click', (p) => {
    if (p.dataType === 'node') focusNode(p.name)
  })
}
</script>

<template>
  <div class="graph-page">
    <!-- 左侧：查询与信息面板 -->
    <aside v-show="sideOpen" class="side">
      <div class="side-head">
        <div class="side-title">知识图谱<span class="dot"></span></div>
        <el-button class="back-btn" text bg size="small" @click="router.push('/')">
          <el-icon style="margin-right: 4px"><Back /></el-icon>返回对话
        </el-button>
      </div>
      <p class="side-desc">搜索实体、点击节点查看关联，探索汽车物流领域概念之间的联系。</p>

      <!-- 统计卡片 -->
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value">{{ totals.nodes }}</div>
          <div class="stat-label">实体总数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ totals.links }}</div>
          <div class="stat-label">关系总数</div>
        </div>
      </div>

      <!-- 搜索 -->
      <div class="section">
        <div class="section-title">查询</div>
        <div class="search-row">
          <el-input
            v-model="keyword"
            placeholder="如：库存、JIT、第三方物流"
            clearable
            @keyup.enter="handleSearch()"
            @keyup.esc="handleEsc"
            @clear="clearSearch"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-button type="primary" :loading="loading" @click="handleSearch()">查询</el-button>
        </div>
        <div class="search-hint">回车查询 · Esc 清空返回全图</div>
        <div v-if="viewHistory.length || searching" class="nav-row">
          <el-button v-if="viewHistory.length" round @click="goBack">
            <el-icon style="margin-right: 4px"><Back /></el-icon>返回上一级
          </el-button>
          <el-button v-if="searching" round @click="clearSearch">
            <el-icon style="margin-right: 4px"><RefreshLeft /></el-icon>返回全图
          </el-button>
        </div>
        <div v-if="matchedList.length" class="matched-list">
          <div class="matched-title">匹配到 {{ matchedList.length }} 个实体（橙色）：</div>
          <div
            v-for="m in matchedList"
            :key="m"
            class="list-item"
            @click="expandEntity(m)"
          >
            <span class="item-name">{{ m }}</span>
            <span class="item-badge">展开</span>
          </div>
        </div>
      </div>

      <!-- 选中详情 -->
      <div class="section">
        <div class="section-title">节点详情</div>
        <div v-if="!selected" class="empty-tip">点击图中的节点查看详情</div>
        <div v-else class="detail-card">
          <div class="detail-name">{{ selected.name }}</div>
          <div class="detail-degree">关联度：{{ selected.degree }}</div>
          <div v-if="selectedRels.length" class="detail-rels">
            <div v-for="(r, i) in selectedRels" :key="i" class="rel-row">
              <span class="rel-other" @click="expandEntity(r.other)">{{ r.other }}</span>
              <span class="rel-type">{{ r.out ? `—[${r.type}]→` : `←[${r.type}]—` }}</span>
            </div>
          </div>
          <div v-else class="empty-tip">暂无关联关系</div>
        </div>
      </div>

      <!-- Top 实体 -->
      <div class="section">
        <div class="section-title">核心实体</div>
        <div
          v-for="n in topEntities"
          :key="n.name"
          class="list-item"
          @click="expandEntity(n.name)"
        >
          <span class="item-name">{{ n.name }}</span>
          <span class="item-badge">{{ n.degree }}</span>
        </div>
      </div>

      <!-- 关系类型 -->
      <div class="section">
        <div class="section-title">常见关系</div>
        <div v-for="t in topRelTypes" :key="t.type" class="list-item">
          <span class="item-name">{{ t.type }}</span>
          <span class="item-badge">{{ t.count }}</span>
        </div>
      </div>
    </aside>

    <!-- 右侧：图谱主区 -->
    <main class="main">
      <div class="float-bar">
        <el-tooltip :content="sideOpen ? '收起面板' : '展开面板'" placement="bottom" :show-after="300">
          <el-button circle @click="sideOpen = !sideOpen">
            <el-icon><component :is="sideOpen ? 'Fold' : 'Expand'" /></el-icon>
          </el-button>
        </el-tooltip>
      </div>
      <div v-loading="loading" class="chart-wrap">
        <div ref="chartRef" class="chart"></div>
        <div class="zoom-hint">滚轮缩放 · 拖拽平移 · 点击节点聚焦关联，点空白处恢复</div>
      </div>
    </main>
  </div>
</template>

<style scoped>
/* 整页布局：侧栏 + 主图区，均占满视口 */
.graph-page {
  height: 100vh;
  display: flex;
  overflow: hidden;
  background: #f8fafc;
}

/* ===== 侧栏 ===== */
.side {
  width: 320px;
  flex-shrink: 0;
  height: 100%;
  overflow-y: auto;
  background: #fff;
  border-right: 1px solid #e6e8ee;
  padding: 16px 14px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.side-title {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 1px;
  color: var(--text, #0f172a);
  display: inline-flex;
  align-items: baseline;
}

.side-title .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: conic-gradient(#ff6b6b, #feca57, #48dbfb, #1dd1a1, #ff6b6b);
  margin-left: 5px;
  align-self: flex-end;
  margin-bottom: 4px;
}

.side-desc {
  margin: -6px 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-muted, #64748b);
}

/* 统计卡片 */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.stat-card {
  background: #f5f7fb;
  border: 1px solid #e6e8ee;
  border-radius: 10px;
  padding: 10px 12px;
}

.stat-value {
  font-size: 20px;
  font-weight: 700;
  color: #2563eb;
}

.stat-label {
  font-size: 12px;
  color: var(--text-muted, #64748b);
  margin-top: 2px;
}

/* 区块 */
.section {
  border-top: 1px solid #eef0f5;
  padding-top: 12px;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text, #0f172a);
  margin-bottom: 8px;
}

.search-row {
  display: flex;
  gap: 8px;
}

.search-row .el-input {
  flex: 1;
}

.search-hint {
  margin-top: 6px;
  font-size: 11.5px;
  color: var(--text-muted, #94a3b8);
}

/* 视图导航：返回上一级 / 返回全图，平分一行 */
.nav-row {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.nav-row .el-button {
  flex: 1;
  margin-left: 0;
}

/* 通用列表行 */
.list-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  font-size: 13px;
  color: var(--text, #0f172a);
  cursor: pointer;
  transition: background 0.15s;
}

.list-item:hover {
  background: #f1f4f9;
}

.item-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-badge {
  flex-shrink: 0;
  font-size: 11.5px;
  color: #2563eb;
  background: #eef3ff;
  border-radius: 999px;
  padding: 1px 8px;
}

/* 搜索匹配列表 */
.matched-list {
  margin-top: 8px;
  max-height: 180px;
  overflow-y: auto;
}

.matched-title {
  font-size: 12px;
  color: var(--text-muted, #64748b);
  margin-bottom: 4px;
}

/* 节点详情 */
.empty-tip {
  font-size: 12.5px;
  color: var(--text-muted, #94a3b8);
  padding: 4px 2px;
}

.detail-card {
  background: #f5f7fb;
  border: 1px solid #e6e8ee;
  border-radius: 10px;
  padding: 10px 12px;
}

.detail-name {
  font-size: 14.5px;
  font-weight: 700;
  color: #0f172a;
  word-break: break-all;
}

.detail-degree {
  font-size: 12px;
  color: #2563eb;
  margin: 4px 0 8px;
}

.detail-rels {
  border-top: 1px dashed #dde2ea;
  padding-top: 6px;
  max-height: 220px;
  overflow-y: auto;
}

.rel-row {
  font-size: 12.5px;
  color: #334155;
  padding: 3px 0;
  line-height: 1.5;
}

.rel-other {
  color: #2563eb;
  cursor: pointer;
}

.rel-other:hover {
  text-decoration: underline;
}

.rel-type {
  color: #94a3b8;
  margin-left: 6px;
  font-size: 11.5px;
}

/* ===== 主图区 ===== */
.main {
  flex: 1;
  min-width: 0;
  position: relative;
  display: flex;
  flex-direction: column;
}

.float-bar {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 8px;
}

.chart-wrap {
  flex: 1;
  min-height: 0;
  position: relative;
}

.chart {
  width: 100%;
  height: 100%;
}

/* 底部操作提示：不挡鼠标操作 */
.zoom-hint {
  position: absolute;
  bottom: 14px;
  left: 50%;
  transform: translateX(-50%);
  pointer-events: none;
  background: rgba(15, 23, 42, 0.55);
  color: #e2e8f0;
  font-size: 12px;
  padding: 4px 14px;
  border-radius: 999px;
  backdrop-filter: blur(4px);
  user-select: none;
}
</style>
