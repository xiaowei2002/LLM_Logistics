<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, RefreshLeft, Search } from '@element-plus/icons-vue'
import * as echarts from 'echarts'

import { fetchOverview, searchGraph } from '@/services/graph'

const router = useRouter()
const chartRef = ref(null)
const keyword = ref('')
const stats = ref('')
const loading = ref(false)
const searching = ref(false)

let chart = null

onMounted(() => {
  chart = echarts.init(chartRef.value)
  window.addEventListener('resize', handleResize)
  loadOverview()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

function handleResize() {
  chart?.resize()
}

/* 全图概览：关联最强的 150 个节点 */
async function loadOverview() {
  loading.value = true
  try {
    const data = await fetchOverview()
    stats.value = `全图共 ${data.totalNodes} 个实体、${data.totalLinks} 条关系，当前展示关联最强的 ${data.nodes.length} 个`
    searching.value = false
    render(data, new Set())
  } catch (e) {
    ElMessage.error('图谱加载失败：' + e.message)
  } finally {
    loading.value = false
  }
}

/* 关键词查询：匹配实体 + 一跳邻居子图 */
async function handleSearch() {
  const q = keyword.value.trim()
  if (!q || loading.value) return
  loading.value = true
  try {
    const data = await searchGraph(q)
    if (!data.nodes.length) {
      ElMessage.info(`没有找到与「${q}」相关的实体`)
      return
    }
    stats.value = `「${q}」匹配 ${data.matched.length} 个实体，展示 ${data.nodes.length} 个节点、${data.links.length} 条关系（橙色为匹配项）`
    searching.value = true
    render(data, new Set(data.matched))
  } catch (e) {
    ElMessage.error('查询失败：' + e.message)
  } finally {
    loading.value = false
  }
}

function backToOverview() {
  keyword.value = ''
  loadOverview()
}

/* 后端数据转 ECharts 力导向图配置 */
function render(data, matched) {
  const maxDegree = Math.max(...data.nodes.map((n) => n.degree), 1)
  const nodes = data.nodes.map((n) => {
    const ratio = n.degree / maxDegree
    return {
      name: n.name,
      value: n.degree,
      symbolSize: 10 + Math.sqrt(n.degree) * 5,
      itemStyle: {
        // 匹配项橙色高亮；普通节点按度数从浅蓝到深蓝
        color: matched.has(n.name) ? '#f97316' : `hsl(221, 75%, ${74 - ratio * 32}%)`,
      },
      label: {
        show: data.nodes.length <= 60 || matched.has(n.name) || n.degree >= Math.max(3, maxDegree * 0.2),
      },
    }
  })
  const links = data.links.map((l) => ({
    source: l.source,
    target: l.target,
    value: l.relType,
  }))

  chart.setOption(
    {
      tooltip: {
        formatter: (p) =>
          p.dataType === 'edge'
            ? `${p.data.source} <b>—[${p.data.value}]→</b> ${p.data.target}`
            : `<b>${p.name}</b><br/>关联度：${p.value}`,
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          data: nodes,
          links,
          force: { repulsion: 170, gravity: 0.08, edgeLength: [40, 120], layoutAnimation: true },
          label: { position: 'right', fontSize: 11, color: '#3a3f4b' },
          lineStyle: { color: '#c4cad4', width: 1, curveness: 0.15, opacity: 0.7 },
          emphasis: {
            focus: 'adjacency',
            lineStyle: { width: 2.5, color: '#2563eb' },
          },
        },
      ],
    },
    true,
  )

  chart.off('click')
  chart.on('click', (p) => {
    // 点击节点 = 以它为关键词展开关联子图
    if (p.dataType === 'node' && !loading.value) {
      keyword.value = p.name
      handleSearch()
    }
  })
}
</script>

<template>
  <div class="graph-page">
    <header class="graph-header">
      <div class="head-left">
        <el-tooltip content="返回对话" placement="bottom" :show-after="300">
          <el-button class="back-btn" circle text @click="router.push('/')">
            <el-icon><Back /></el-icon>
          </el-button>
        </el-tooltip>
        <div class="graph-title">知识图谱<span class="dot"></span></div>
      </div>

      <div class="head-search">
        <el-input
          v-model="keyword"
          class="search-box"
          placeholder="搜索实体，如：库存、JIT、第三方物流"
          clearable
          @keyup.enter="handleSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" :loading="loading" @click="handleSearch">查询</el-button>
        <el-button v-if="searching" @click="backToOverview">
          <el-icon style="margin-right: 4px"><RefreshLeft /></el-icon>返回全图
        </el-button>
      </div>
    </header>

    <div class="stats-bar">
      <span>{{ stats }}</span>
      <span class="hint">拖拽 / 滚轮缩放 · 点击节点展开关联</span>
    </div>

    <main v-loading="loading" class="graph-main">
      <div ref="chartRef" class="chart"></div>
    </main>
  </div>
</template>

<style scoped>
/* 整页布局：header + stats 固定高度，图表占满剩余空间 */
.graph-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #f5f6f9;
}

.graph-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 16px;
  background: #fff;
  border-bottom: 1px solid #e6e8ee;
  flex-shrink: 0;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.back-btn {
  color: var(--text-muted);
}

.graph-title {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 1px;
  color: var(--text);
  display: inline-flex;
  align-items: baseline;
}

.graph-title .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: conic-gradient(#ff6b6b, #feca57, #48dbfb, #1dd1a1, #ff6b6b);
  margin-left: 5px;
  align-self: flex-end;
  margin-bottom: 4px;
}

.head-search {
  display: flex;
  align-items: center;
  gap: 8px;
}

.search-box {
  width: 320px;
}

.search-box :deep(.el-input__wrapper) {
  border-radius: 999px;
}

.stats-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 16px;
  font-size: 12.5px;
  color: var(--text-muted);
  background: #fff;
  border-bottom: 1px solid #e6e8ee;
  flex-shrink: 0;
}

.stats-bar .hint {
  flex-shrink: 0;
  opacity: 0.75;
}

.graph-main {
  flex: 1;
  min-height: 0;
}

.chart {
  width: 100%;
  height: 100%;
}
</style>
