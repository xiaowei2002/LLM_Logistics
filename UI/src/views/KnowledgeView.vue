<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Back,
  Delete,
  Document,
  Refresh,
  UploadFilled,
  View,
} from '@element-plus/icons-vue'

import {
  deleteDocument,
  fetchDocuments,
  fetchFormats,
  fetchTask,
  uploadDocument,
} from '@/services/rag'

const router = useRouter()

const documents = ref([])
const formats = ref([])
const loadingList = ref(false)
const uploadingName = ref('')
const dragOver = ref(false)
const fileInput = ref(null)
const serviceError = ref('')

/* 分块详情：列表接口只给元数据，分块内容按需拉 /tasks/{id} */
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

/* 解析状态：后端的 pending / running / done / failed 直接映射到标签 */
const STATUS_TEXT = { pending: '排队中', running: '解析中', done: '已完成', failed: '失败' }
const STATUS_TYPE = { pending: 'info', running: 'warning', done: 'success', failed: 'danger' }

const formatText = computed(() => formats.value.map((f) => f.replace('.', '')).join(' / '))

const hasPending = computed(() =>
  documents.value.some((d) => d.status === 'pending' || d.status === 'running'),
)

function formatSize(bytes) {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/* ===== 列表 & 轮询 ===== */
const POLL_MS = 1500
let timer = null

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

async function tick() {
  const pending = documents.value.filter((d) => d.status === 'pending' || d.status === 'running')
  if (!pending.length) {
    stopPolling()
    return
  }
  await Promise.all(
    pending.map(async (doc) => {
      try {
        const task = await fetchTask(doc.task_id)
        doc.status = task.status
        doc.chunk_count = task.chunk_count
        doc.error = task.error
        doc.finished_at = task.finished_at
      } catch {
        /* 单条查询失败先跳过，下一轮再试 */
      }
    }),
  )
  if (!hasPending.value) stopPolling()
}

async function refresh() {
  loadingList.value = true
  try {
    const res = await fetchDocuments()
    documents.value = res.documents || []
    serviceError.value = ''
    if (hasPending.value) startPolling()
  } catch (err) {
    serviceError.value = err.message
  } finally {
    loadingList.value = false
  }
}

function startPolling() {
  if (timer) return
  timer = setInterval(tick, POLL_MS)
}

/* ===== 上传 ===== */
function pickFiles() {
  fileInput.value?.click()
}

function onPick(event) {
  uploadFiles(event.target.files)
  event.target.value = '' // 允许重复选择同一个文件
}

function onDrop(event) {
  dragOver.value = false
  uploadFiles(event.dataTransfer?.files)
}

async function uploadFiles(files) {
  const list = Array.from(files || [])
  if (!list.length || uploadingName.value) return

  for (const file of list) {
    const ext = `.${file.name.split('.').pop().toLowerCase()}`
    if (formats.value.length && !formats.value.includes(ext)) {
      ElMessage.warning(`跳过「${file.name}」：不支持 ${ext} 类型`)
      continue
    }
    uploadingName.value = file.name
    try {
      await uploadDocument(file)
      ElMessage.success(`「${file.name}」已上传，正在后台解析`)
    } catch (err) {
      ElMessage.error(`「${file.name}」上传失败：${err.message}`)
    } finally {
      uploadingName.value = ''
    }
  }
  await refresh()
}

/* ===== 分块详情 ===== */
async function openDetail(row) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await fetchTask(row.task_id)
  } catch (err) {
    ElMessage.error(`获取分块失败：${err.message}`)
  } finally {
    detailLoading.value = false
  }
}

/* ===== 删除 ===== */
async function remove(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除「${row.file}」？服务器上的原文件与解析结果都会一并删除。`,
      '删除文档',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  try {
    await deleteDocument(row.task_id)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    ElMessage.error(`删除失败：${err.message}`)
  }
}

onMounted(async () => {
  try {
    const res = await fetchFormats()
    formats.value = res.supported_formats || []
  } catch {
    /* 拿不到格式列表不影响上传，交给后端校验 */
  }
  await refresh()
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="kb-page">
    <!-- 顶栏：返回 / 刷新 + 居中标题 -->
    <header class="top-head">
      <div class="bar-left">
        <el-button text bg size="small" @click="router.push('/')">
          <el-icon style="margin-right: 4px"><Back /></el-icon>返回对话
        </el-button>
      </div>
      <div class="head-title">知识库管理</div>
      <div class="bar-right">
        <el-button text bg size="small" :disabled="loadingList" @click="refresh">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>刷新
        </el-button>
      </div>
    </header>

    <main class="body">
      <!-- 服务未启动提示 -->
      <el-alert
        v-if="serviceError"
        class="svc-alert"
        type="error"
        :closable="false"
        show-icon
        title="知识库服务连不上（端口 8000）"
      >
        <template #default>
          <div class="svc-tip">
            {{ serviceError }}<br />
            在 backend 目录下启动：<code>python -m uvicorn core.tools.mrag.api.routes.api:app --port 8000</code>
          </div>
        </template>
      </el-alert>

      <!-- 上传区：拖拽或点击 -->
      <section
        class="drop-zone"
        :class="{ over: dragOver, busy: uploadingName }"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="onDrop"
        @click="pickFiles"
      >
        <input ref="fileInput" type="file" multiple hidden @change="onPick" />
        <el-icon class="dz-icon"><UploadFilled /></el-icon>
        <div class="dz-title">
          {{ uploadingName ? `正在上传「${uploadingName}」…` : '把文件拖到这里，或点击选择' }}
        </div>
        <div class="dz-sub">支持 {{ formatText || '多种文档格式' }}</div>
      </section>

      <!-- 文档列表 -->
      <section class="list-wrap">
        <div class="list-head">
          <span class="list-title">文档列表</span>
          <span class="list-count">{{ documents.length }} 份</span>
        </div>

        <el-table
          v-loading="loadingList"
          :data="documents"
          class="kb-table"
          empty-text="还没有文档，先拖一个上来试试"
        >
          <el-table-column label="文件名" min-width="220">
            <template #default="{ row }">
              <div class="file-cell">
                <el-icon class="file-icon"><Document /></el-icon>
                <span class="file-name">{{ row.file }}</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="大小" width="100">
            <template #default="{ row }">{{ formatSize(row.size) }}</template>
          </el-table-column>

          <el-table-column label="分块" width="80">
            <template #default="{ row }">
              {{ row.status === 'done' ? row.chunk_count : '—' }}
            </template>
          </el-table-column>

          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tooltip
                :disabled="row.status !== 'failed'"
                :content="row.error || '解析失败'"
                placement="top"
              >
                <el-tag size="small" :type="STATUS_TYPE[row.status] || 'info'" effect="light">
                  {{ STATUS_TEXT[row.status] || row.status }}
                </el-tag>
              </el-tooltip>
            </template>
          </el-table-column>

          <el-table-column label="上传时间" width="165">
            <template #default="{ row }">{{ row.uploaded_at || '—' }}</template>
          </el-table-column>

          <el-table-column label="操作" width="150" align="right">
            <template #default="{ row }">
              <el-button
                text
                size="small"
                :disabled="row.status !== 'done'"
                @click="openDetail(row)"
              >
                <el-icon style="margin-right: 2px"><View /></el-icon>分块
              </el-button>
              <el-button
                text
                size="small"
                :disabled="row.status === 'running'"
                @click="remove(row)"
              >
                <el-icon style="margin-right: 2px"><Delete /></el-icon>删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <p v-if="hasPending" class="poll-hint">有文档正在解析，页面会自动刷新状态…</p>
      </section>
    </main>

    <!-- 分块详情 -->
    <el-dialog v-model="detailOpen" :title="detail?.file || '分块详情'" width="720px">
      <div v-loading="detailLoading" class="detail-body">
        <template v-if="detail">
          <div class="detail-meta">
            共 {{ detail.chunk_count ?? detail.chunks?.length ?? 0 }} 个分块 ·
            大小 {{ formatSize(detail.size) }} · 上传于 {{ detail.uploaded_at || '—' }}
          </div>
          <div v-for="(c, i) in detail.chunks || []" :key="c.chunk_id || i" class="chunk">
            <div class="chunk-head">
              <span class="chunk-idx">#{{ i + 1 }}</span>
              <el-tag size="small" effect="plain">{{ c.type }}</el-tag>
            </div>
            <pre class="chunk-text">{{ c.content }}</pre>
          </div>
          <el-empty v-if="!(detail.chunks || []).length" description="没有分块内容" />
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.kb-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
}

/* ===== 顶栏 ===== */
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

.bar-right {
  margin-left: auto;
}

.head-title {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

/* ===== 主体：可滚动 ===== */
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  width: 100%;
  max-width: 900px;
  margin: 0 auto;
  padding: 16px 20px 32px;
  scrollbar-width: thin;
  scrollbar-color: #d3d7de transparent;
}

.body::-webkit-scrollbar {
  width: 6px;
}

.body::-webkit-scrollbar-thumb {
  background: #d3d7de;
  border-radius: 3px;
}

.svc-alert {
  margin-bottom: 14px;
}

.svc-tip {
  font-size: 12.5px;
  line-height: 1.7;
}

.svc-tip code {
  background: var(--fill-light);
  border-radius: 4px;
  padding: 1px 5px;
  font-family: Consolas, Monaco, monospace;
}

/* ===== 上传区 ===== */
.drop-zone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 28px 20px;
  border: 2px dashed var(--border);
  border-radius: 14px;
  background: var(--bg);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.drop-zone:hover {
  border-color: var(--primary);
}

.drop-zone.over {
  border-color: var(--primary);
  background: var(--primary-light);
}

.drop-zone.busy {
  cursor: default;
  border-color: var(--primary);
}

.dz-icon {
  font-size: 30px;
  color: var(--primary);
}

.dz-title {
  font-size: 14px;
  color: var(--text);
}

.dz-sub {
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
  line-height: 1.6;
}

/* ===== 列表 ===== */
.list-wrap {
  margin-top: 22px;
}

.list-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 10px;
}

.list-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.list-count {
  font-size: 12px;
  color: var(--text-muted);
}

.kb-table {
  width: 100%;
}

.file-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.file-icon {
  color: var(--text-muted);
  flex-shrink: 0;
}

.file-name {
  word-break: break-all;
}

.poll-hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--text-muted);
}

/* ===== 分块详情 ===== */
.detail-body {
  max-height: 60vh;
  overflow-y: auto;
}

.detail-meta {
  font-size: 12.5px;
  color: var(--text-muted);
  margin-bottom: 12px;
}

.chunk + .chunk {
  margin-top: 14px;
}

.chunk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.chunk-idx {
  font-size: 12px;
  color: var(--text-muted);
}

.chunk-text {
  margin: 0;
  padding: 10px 12px;
  background: var(--fill-light);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
  font-family: inherit;
}
</style>
