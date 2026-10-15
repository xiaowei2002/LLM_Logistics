<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  Collection,
  Delete,
  Document,
  Link,
  Plus,
  Promotion,
  Refresh,
  UploadFilled,
  View,
} from '@element-plus/icons-vue'

import {
  addFiles,
  addWebUrl,
  createKnowledgeBase,
  deleteFiles,
  deleteKnowledgeBase,
  listFileChunks,
  listKbFiles,
  listKnowledgeBases,
  streamQuery,
  toProxyUrl,
  uploadFile,
} from '@/services/rag'
import { renderMarkdown } from '@/utils/markdown'
import AppNav from '@/components/AppNav.vue'

/* 解析器支持的类型（document/parser.py 的 get_document_parser）与上传大小上限 */
const ACCEPT = ['.pdf', '.docx', '.md', '.png', '.jpg', '.jpeg']
const MAX_SIZE = 50 * 1024 * 1024

const STATUS = {
  PENDING: { text: '排队中', type: 'info' },
  RUNNING: { text: '解析中', type: 'warning' },
  SUCCESS: { text: '已完成', type: 'success' },
  FAILED: { text: '失败', type: 'danger' },
}

const serviceError = ref('')

/* ===== 知识库 ===== */
const kbs = ref([])
const kbLoading = ref(false)
const activeKbId = ref('')
const activeKb = computed(() => kbs.value.find((kb) => kb.kb_id === activeKbId.value))

const createOpen = ref(false)
const creating = ref(false)
const createForm = ref({ name: '', desc: '' })

async function loadKbs() {
  kbLoading.value = true
  try {
    kbs.value = await listKnowledgeBases()
    serviceError.value = ''
    if (!kbs.value.some((kb) => kb.kb_id === activeKbId.value)) {
      activeKbId.value = kbs.value[0]?.kb_id || ''
    }
  } catch (err) {
    serviceError.value = err.message
  } finally {
    kbLoading.value = false
  }
}

async function submitCreate() {
  const name = createForm.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写知识库名称')
    return
  }
  creating.value = true
  try {
    const kb = await createKnowledgeBase({ name, desc: createForm.value.desc.trim() })
    createOpen.value = false
    createForm.value = { name: '', desc: '' }
    await loadKbs()
    activeKbId.value = kb.kb_id
    ElMessage.success('知识库已创建')
  } catch (err) {
    ElMessage.error(`创建失败：${err.message}`)
  } finally {
    creating.value = false
  }
}

async function removeKb(kb) {
  try {
    await ElMessageBox.confirm(
      `确定删除知识库「${kb.kb_name || kb.kb_id}」？其中所有文档的向量索引都会被清除。`,
      '删除知识库',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteKnowledgeBase(kb.kb_id)
    ElMessage.success('已删除')
    await loadKbs()
  } catch (err) {
    ElMessage.error(`删除失败：${err.message}`)
  }
}

/* ===== 文件列表 & 轮询 ===== */
const files = ref([])
const total = ref(0)
const pageNo = ref(1)
const pageSize = ref(10)
const filesLoading = ref(false)
const selected = ref([])

const processing = computed(() => files.value.some((f) => ['PENDING', 'RUNNING'].includes(f.file_status)))

async function loadFiles({ silent = false } = {}) {
  if (!activeKbId.value) {
    files.value = []
    total.value = 0
    return
  }
  if (!silent) filesLoading.value = true
  try {
    const data = await listKbFiles(activeKbId.value, pageNo.value, pageSize.value)
    files.value = data.records || []
    total.value = data.total || 0
    serviceError.value = ''
  } catch (err) {
    serviceError.value = err.message
  } finally {
    filesLoading.value = false
  }
}

/* 有文件在排队 / 解析时每 3 秒静默刷新一次 */
let timer = null
watch(processing, (on) => {
  clearInterval(timer)
  timer = on ? setInterval(() => loadFiles({ silent: true }), 3000) : null
})

watch(activeKbId, () => {
  pageNo.value = 1
  selected.value = []
  messages.value = []
  loadFiles()
})

function refresh() {
  loadKbs().then(() => loadFiles())
}

/* ===== 上传 ===== */
const fileInput = ref(null)
const dragOver = ref(false)
const uploading = ref('')

function fileExt(name) {
  const idx = (name || '').lastIndexOf('.')
  return idx >= 0 ? name.slice(idx).toLowerCase() : ''
}

function onPick(event) {
  uploadFiles(event.target.files)
  event.target.value = '' // 允许重复选择同一个文件
}

function onDrop(event) {
  dragOver.value = false
  uploadFiles(event.dataTransfer?.files)
}

async function uploadFiles(fileList) {
  const list = Array.from(fileList || [])
  if (!list.length || uploading.value || !activeKbId.value) return

  const uploaded = []
  for (const file of list) {
    if (!ACCEPT.includes(fileExt(file.name))) {
      ElMessage.warning(`跳过「${file.name}」：暂不支持该类型`)
      continue
    }
    if (file.size > MAX_SIZE) {
      ElMessage.warning(`跳过「${file.name}」：超过 50MB`)
      continue
    }
    uploading.value = file.name
    try {
      const data = await uploadFile(file)
      uploaded.push({ filename: data.original_filename || file.name, file_url: data.permanent_url })
    } catch (err) {
      ElMessage.error(`「${file.name}」上传失败：${err.message}`)
    }
  }
  uploading.value = ''
  if (!uploaded.length) return

  try {
    await addFiles(activeKbId.value, uploaded)
    ElMessage.success(`已提交 ${uploaded.length} 个文件，正在后台解析`)
  } catch (err) {
    ElMessage.error(`提交解析失败：${err.message}`)
  }
  await loadFiles()
}

/* ===== 网页 ===== */
const urlOpen = ref(false)
const urlValue = ref('')
const addingUrl = ref(false)

async function submitUrl() {
  const url = urlValue.value.trim()
  if (!/^https?:\/\//i.test(url)) {
    ElMessage.warning('请输入以 http:// 或 https:// 开头的网址')
    return
  }
  addingUrl.value = true
  try {
    await addWebUrl(activeKbId.value, url)
    urlOpen.value = false
    urlValue.value = ''
    ElMessage.success('网页已提交，正在后台抓取解析')
    await loadFiles()
  } catch (err) {
    ElMessage.error(`添加失败：${err.message}`)
  } finally {
    addingUrl.value = false
  }
}

/* ===== 删除文件 ===== */
async function removeFiles(rows) {
  const names = rows.map((r) => `「${r.title || r.file_url}」`).join('、')
  try {
    await ElMessageBox.confirm(`确定删除${rows.length > 1 ? ` ${rows.length} 个文件` : names}？对应的向量索引会一并清除。`, '删除文件', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteFiles(activeKbId.value, rows.map((r) => r.file_id))
    ElMessage.success('已删除')
    selected.value = []
    if (files.value.length === rows.length && pageNo.value > 1) pageNo.value -= 1
    await loadFiles()
  } catch (err) {
    ElMessage.error(`删除失败：${err.message}`)
  }
}

/* ===== 分块详情 ===== */
const chunkOpen = ref(false)
const chunkLoading = ref(false)
const chunkFile = ref(null)
const chunks = ref({ texts: [], images: [] })

async function openChunks(row) {
  chunkFile.value = row
  chunkOpen.value = true
  chunkLoading.value = true
  chunks.value = { texts: [], images: [] }
  try {
    const data = await listFileChunks(activeKbId.value, row.file_id)
    chunks.value = { texts: data.texts || [], images: (data.images || []).map(toProxyUrl) }
  } catch (err) {
    ElMessage.error(`获取分块失败：${err.message}`)
  } finally {
    chunkLoading.value = false
  }
}

/* ===== 检索问答 ===== */
const activeTab = ref('files')
const messages = ref([])
const question = ref('')
const asking = ref(false)
const chatEl = ref(null)
let abortController = null

const samples = ['零部件到货后的入库流程是什么？', '总结一下知识库里关于仓储安全的要求', '质检不合格的物料如何处理？']

async function scrollChat() {
  await nextTick()
  if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
}

async function ask(text = question.value) {
  const q = text.trim()
  if (!q || asking.value || !activeKbId.value) return
  question.value = ''
  messages.value.push({ role: 'user', content: q })
  messages.value.push({ role: 'assistant', content: '', streaming: true })
  const reply = messages.value[messages.value.length - 1]
  asking.value = true
  abortController = new AbortController()
  scrollChat()
  try {
    await streamQuery(
      { kbId: activeKbId.value, question: q },
      (delta) => {
        reply.content += delta
        scrollChat()
      },
      abortController.signal,
    )
    if (!reply.content) reply.content = '没有生成回答，请检查知识库内容或模型配置。'
  } catch (err) {
    if (err.name !== 'AbortError') reply.content += `${reply.content ? '\n\n' : ''}[请求出错：${err.message}]`
  } finally {
    reply.streaming = false
    asking.value = false
    scrollChat()
  }
}

/* 回答里的图片链接（页面截图等）同样改走 /rag 代理 */
function renderAnswer(text) {
  return renderMarkdown(text.replace(/\(https?:\/\/(?:127\.0\.0\.1|localhost):8000\//g, '(/rag/'))
}

/* ===== 工具 ===== */
function formatTime(value) {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}

/* 文件按扩展名显示类型；网页条目没有标题，显示为 web */
function fileKind(row) {
  const ext = fileExt(row.title)
  return ext ? ext.slice(1) : 'web'
}

// 选中知识库后由 activeKbId 的 watch 加载文件列表
onMounted(loadKbs)

onUnmounted(() => {
  clearInterval(timer)
  abortController?.abort()
})
</script>

<template>
  <div class="kb-page">
    <AppNav>
      <template #actions>
        <el-button text bg size="small" :disabled="kbLoading || filesLoading" @click="refresh">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </AppNav>

    <div class="kb-body">
      <!-- 左侧：知识库列表 -->
      <aside class="kb-side">
        <div class="side-head">
          <span>知识库</span>
          <el-button type="primary" size="small" :icon="Plus" @click="createOpen = true">新建</el-button>
        </div>
        <div v-loading="kbLoading" class="kb-list">
          <button
            v-for="kb in kbs"
            :key="kb.kb_id"
            type="button"
            class="kb-item"
            :class="{ active: kb.kb_id === activeKbId }"
            @click="activeKbId = kb.kb_id"
          >
            <span class="kb-icon"><el-icon><Collection /></el-icon></span>
            <span class="kb-text">
              <b>{{ kb.kb_name || kb.kb_id }}</b>
              <small>{{ kb.kb_desc || '暂无描述' }}</small>
            </span>
            <span class="kb-del" role="button" title="删除知识库" @click.stop="removeKb(kb)"><el-icon><Delete /></el-icon></span>
          </button>
          <el-empty v-if="!kbLoading && !kbs.length" :image-size="64" description="还没有知识库" />
        </div>
      </aside>

      <!-- 右侧：当前知识库 -->
      <main class="kb-main">
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
              在 backend 目录下启动：<code>python -m uvicorn core.tools.mrag.api.app:app --port 8000</code>
            </div>
          </template>
        </el-alert>

        <template v-if="activeKb">
          <header class="main-head">
            <div>
              <h1>{{ activeKb.kb_name || activeKb.kb_id }}</h1>
              <p>{{ activeKb.kb_desc || '暂无描述' }} · 创建于 {{ formatTime(activeKb.create_time) }}</p>
            </div>
            <div class="head-stats">
              <div><b>{{ total }}</b><span>文档</span></div>
              <div v-if="processing" class="stat-busy"><b class="pulse"></b><span>解析中</span></div>
            </div>
          </header>

          <el-tabs v-model="activeTab" class="kb-tabs">
            <!-- 文档管理 -->
            <el-tab-pane label="文档管理" name="files">
              <section
                class="drop-zone"
                :class="{ over: dragOver, busy: uploading }"
                @dragover.prevent="dragOver = true"
                @dragleave.prevent="dragOver = false"
                @drop.prevent="onDrop"
                @click="fileInput?.click()"
              >
                <input ref="fileInput" type="file" multiple hidden :accept="ACCEPT.join(',')" @change="onPick" />
                <span class="dz-icon"><el-icon><UploadFilled /></el-icon></span>
                <div class="dz-text">
                  <b>{{ uploading ? `正在上传「${uploading}」…` : '拖拽文件到这里，或点击选择' }}</b>
                  <small>支持 PDF / Word(docx) / Markdown / 图片(png、jpg)，单个不超过 50MB；上传后自动完成版面解析、切分与多模态向量化</small>
                </div>
                <el-button class="dz-url" :icon="Link" @click.stop="urlOpen = true">添加网页</el-button>
              </section>

              <div class="table-bar">
                <span>文档列表</span>
                <el-button
                  v-if="selected.length"
                  size="small"
                  type="danger"
                  plain
                  :icon="Delete"
                  @click="removeFiles(selected)"
                >删除所选（{{ selected.length }}）</el-button>
              </div>

              <el-table
                v-loading="filesLoading"
                :data="files"
                row-key="file_id"
                class="kb-table"
                empty-text="还没有文档，先拖一个上来试试"
                @selection-change="selected = $event"
              >
                <el-table-column type="selection" width="42" />
                <el-table-column label="名称" min-width="260">
                  <template #default="{ row }">
                    <div class="file-cell">
                      <span class="file-badge" :class="fileKind(row)">{{ fileKind(row) }}</span>
                      <span class="file-name" :title="row.title || row.file_url">{{ row.title || row.file_url }}</span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="110">
                  <template #default="{ row }">
                    <el-tag size="small" :type="STATUS[row.file_status]?.type || 'info'" effect="light" round>
                      <span v-if="['PENDING', 'RUNNING'].includes(row.file_status)" class="tag-dot"></span>
                      {{ STATUS[row.file_status]?.text || row.file_status }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="添加时间" width="170">
                  <template #default="{ row }">{{ formatTime(row.create_time) }}</template>
                </el-table-column>
                <el-table-column label="操作" width="160" align="right">
                  <template #default="{ row }">
                    <el-button text size="small" :icon="View" :disabled="row.file_status !== 'SUCCESS'" @click="openChunks(row)">分块</el-button>
                    <el-button text size="small" :icon="Delete" :disabled="row.file_status === 'RUNNING'" @click="removeFiles([row])">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>

              <div class="table-foot">
                <span v-if="processing" class="poll-hint">有文档正在解析，状态会自动刷新…</span>
                <el-pagination
                  v-if="total > pageSize"
                  v-model:current-page="pageNo"
                  :page-size="pageSize"
                  :total="total"
                  layout="prev, pager, next"
                  size="small"
                  @current-change="loadFiles()"
                />
              </div>
            </el-tab-pane>

            <!-- 检索问答 -->
            <el-tab-pane label="检索问答" name="query">
              <div class="qa">
                <div ref="chatEl" class="qa-flow">
                  <div v-if="!messages.length" class="qa-empty">
                    <span class="qa-empty-icon"><el-icon><ChatDotRound /></el-icon></span>
                    <b>基于「{{ activeKb.kb_name || activeKb.kb_id }}」提问</b>
                    <small>Agentic RAG：问题扩展 → 多路检索（文本 / 图片 / 页面）→ 重排 → 大模型生成</small>
                    <div class="qa-samples">
                      <button v-for="s in samples" :key="s" type="button" @click="ask(s)">{{ s }}</button>
                    </div>
                  </div>
                  <div v-for="(m, i) in messages" :key="i" class="qa-row" :class="m.role">
                    <div v-if="m.role === 'user'" class="qa-bubble">{{ m.content }}</div>
                    <div v-else class="qa-answer">
                      <span v-if="m.streaming && !m.content" class="dots"><i></i><i></i><i></i></span>
                      <div class="md-content" v-html="renderAnswer(m.content)"></div>
                    </div>
                  </div>
                </div>
                <div class="qa-input">
                  <el-input
                    v-model="question"
                    type="textarea"
                    :rows="1"
                    resize="none"
                    :autosize="{ minRows: 1, maxRows: 4 }"
                    placeholder="输入问题，Enter 发送"
                    @keydown.enter.exact.prevent="ask()"
                  />
                  <el-button v-if="asking" @click="abortController?.abort()">停止</el-button>
                  <el-button v-else type="primary" :icon="Promotion" :disabled="!question.trim()" @click="ask()">提问</el-button>
                </div>
              </div>
            </el-tab-pane>
          </el-tabs>
        </template>

        <el-empty v-else-if="!kbLoading && !serviceError" description="先在左侧新建一个知识库" />
      </main>
    </div>

    <!-- 新建知识库 -->
    <el-dialog v-model="createOpen" title="新建知识库" width="440px">
      <el-form label-position="top" @submit.prevent="submitCreate">
        <el-form-item label="名称" required>
          <el-input v-model="createForm.name" maxlength="40" placeholder="如：仓储作业规范" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createForm.desc" type="textarea" :rows="3" maxlength="200" placeholder="知识库的用途与内容范围" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 添加网页 -->
    <el-dialog v-model="urlOpen" title="添加网页" width="480px">
      <el-input v-model="urlValue" :prefix-icon="Link" placeholder="https://..." @keydown.enter.prevent="submitUrl" />
      <p class="dialog-tip">抓取网页正文转为 Markdown 后入库</p>
      <template #footer>
        <el-button @click="urlOpen = false">取消</el-button>
        <el-button type="primary" :loading="addingUrl" @click="submitUrl">添加</el-button>
      </template>
    </el-dialog>

    <!-- 分块详情 -->
    <el-drawer v-model="chunkOpen" :title="chunkFile?.title || '分块详情'" size="560px">
      <div v-loading="chunkLoading" class="chunk-body">
        <p class="chunk-meta">
          <el-icon><Document /></el-icon>文本分块 {{ chunks.texts.length }} 个 · 图片 {{ chunks.images.length }} 张
        </p>
        <div v-if="chunks.images.length" class="chunk-images">
          <el-image
            v-for="(img, i) in chunks.images"
            :key="img"
            :src="img"
            :preview-src-list="chunks.images"
            :initial-index="i"
            fit="cover"
            preview-teleported
          />
        </div>
        <div v-for="(text, i) in chunks.texts" :key="i" class="chunk">
          <span class="chunk-idx">#{{ i + 1 }}</span>
          <pre class="chunk-text">{{ text }}</pre>
        </div>
        <el-empty v-if="!chunkLoading && !chunks.texts.length && !chunks.images.length" description="没有分块内容" />
      </div>
    </el-drawer>
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

.kb-body {
  flex: 1;
  min-height: 0;
  display: flex;
}

/* ===== 左侧知识库列表 ===== */
.kb-side {
  display: flex;
  flex-direction: column;
  width: 264px;
  flex-shrink: 0;
  padding: 18px 12px;
  border-right: 1px solid #d3dae2;
  background: var(--surface);
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 6px 14px;
  font-size: 15px;
  font-weight: 700;
}

.kb-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.kb-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.kb-item:hover {
  background: #f3f6f9;
}

.kb-item.active {
  border-color: #c8d6e2;
  background: var(--primary-light);
}

.kb-item.active::before {
  content: '';
  position: absolute;
  left: -12px;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: #308d94;
}

.kb-icon {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: #e8eef3;
  color: var(--primary);
  font-size: 17px;
}

.kb-item.active .kb-icon {
  background: var(--primary);
  color: #2ce0b4;
}

.kb-text {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.kb-text b,
.kb-text small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-text b {
  font-size: 13.5px;
}

.kb-text small {
  color: var(--text-muted);
  font-size: 12px;
}

.kb-del {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  color: #8095a1;
  font-size: 17px;
  transition: color 0.15s, background 0.15s;
}

.kb-del:hover {
  color: #d03050;
  background: #fde8ec;
}

/* ===== 右侧主区 ===== */
.kb-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 22px 28px 32px;
}

.svc-alert {
  margin-bottom: 16px;
}

.svc-tip {
  font-size: 12.5px;
  line-height: 1.7;
}

.svc-tip code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--fill-light);
  font-family: Consolas, Monaco, monospace;
}

.main-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 6px;
}

.main-head h1 {
  font-size: 20px;
  font-weight: 700;
}

.main-head p {
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 13px;
}

.head-stats {
  display: flex;
  gap: 10px;
}

.head-stats > div {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: 1px solid #d3dae2;
  border-radius: 8px;
  background: var(--surface);
}

.head-stats b {
  font-size: 18px;
  color: var(--primary);
}

.head-stats span {
  color: var(--text-muted);
  font-size: 12.5px;
}

.stat-busy span {
  color: #b7791f;
}

.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e6a23c;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(230, 162, 60, 0.5); }
  50% { box-shadow: 0 0 0 6px rgba(230, 162, 60, 0); }
}

.kb-tabs :deep(.el-tabs__item) {
  font-size: 14px;
}

/* ===== 上传区 ===== */
.drop-zone {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px 22px;
  border: 1.5px dashed #b3bfc7;
  border-radius: 12px;
  background: var(--surface);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.drop-zone:hover,
.drop-zone.over {
  border-color: var(--primary);
  background: #f6f9fb;
}

.drop-zone.busy {
  cursor: progress;
}

.dz-icon {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: var(--primary-light);
  color: var(--primary);
  font-size: 24px;
}

.drop-zone.busy .dz-icon {
  animation: bob 1s ease-in-out infinite;
}

@keyframes bob {
  50% { transform: translateY(-4px); }
}

.dz-text {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.dz-text b {
  font-size: 14px;
}

.dz-text small {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.dz-url {
  flex-shrink: 0;
}

/* ===== 文件表格 ===== */
.table-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 32px;
  margin: 20px 0 8px;
  font-size: 14px;
  font-weight: 600;
}

.kb-table {
  border: 1px solid #d3dae2;
  border-radius: 10px;
}

.file-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.file-badge {
  flex-shrink: 0;
  min-width: 42px;
  padding: 2px 6px;
  border-radius: 5px;
  background: #e8eef3;
  color: #4d6b7b;
  font-size: 11px;
  font-weight: 700;
  text-align: center;
  text-transform: uppercase;
}

.file-badge.pdf { background: #fdecec; color: #c0392b; }
.file-badge.docx { background: #e8f0fd; color: #2b5fb4; }
.file-badge.md { background: #eef3e8; color: #4f7a28; }
.file-badge.png,
.file-badge.jpg,
.file-badge.jpeg { background: #f3eafb; color: #7b4bb0; }
.file-badge.web { background: #e3f6f3; color: #1f7f74; }

.file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
  background: currentColor;
  vertical-align: middle;
  animation: blink 1s ease-in-out infinite;
}

@keyframes blink {
  50% { opacity: 0.25; }
}

.table-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 40px;
  margin-top: 8px;
}

.poll-hint {
  color: var(--text-muted);
  font-size: 12px;
}

.table-foot .el-pagination {
  margin-left: auto;
}

/* ===== 检索问答 ===== */
.qa {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 56px - 190px);
  min-height: 420px;
  border: 1px solid #d3dae2;
  border-radius: 12px;
  background: var(--surface);
  overflow: hidden;
}

.qa-flow {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 22px;
}

.qa-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 36px 16px;
  text-align: center;
}

.qa-empty-icon {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  margin-bottom: 4px;
  border-radius: 14px;
  background: var(--primary);
  color: #2ce0b4;
  font-size: 24px;
}

.qa-empty small {
  color: var(--text-muted);
  font-size: 12.5px;
}

.qa-samples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 14px;
}

.qa-samples button {
  padding: 7px 14px;
  border: 1px solid #d3dae2;
  border-radius: 999px;
  background: #f7f9fb;
  color: var(--text);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.qa-samples button:hover {
  border-color: var(--primary);
  background: var(--primary-light);
}

.qa-row + .qa-row {
  margin-top: 16px;
}

.qa-row.user {
  display: flex;
  justify-content: flex-end;
}

.qa-bubble {
  max-width: 75%;
  padding: 10px 14px;
  border-radius: 12px 12px 2px 12px;
  background: var(--primary);
  color: #fff;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.qa-answer {
  font-size: 14px;
  line-height: 1.7;
}

.md-content :deep(p) { margin: 6px 0; }
.md-content :deep(ul),
.md-content :deep(ol) { margin: 6px 0; padding-left: 22px; }
.md-content :deep(h1),
.md-content :deep(h2),
.md-content :deep(h3) { margin: 12px 0 6px; font-size: 15.5px; }
.md-content :deep(img) {
  display: block;
  max-width: 100%;
  margin: 10px 0;
  border: 1px solid #d3dae2;
  border-radius: 8px;
}
.md-content :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--fill-light);
  font-size: 13px;
}
.md-content :deep(table) { border-collapse: collapse; margin: 8px 0; font-size: 13px; }
.md-content :deep(th),
.md-content :deep(td) { border: 1px solid #d3dae2; padding: 5px 10px; }

.dots {
  display: inline-flex;
  gap: 5px;
}

.dots i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-muted);
  animation: blink 1.2s infinite ease-in-out;
}

.dots i:nth-child(2) { animation-delay: 0.15s; }
.dots i:nth-child(3) { animation-delay: 0.3s; }

.qa-input {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 12px 14px;
  border-top: 1px solid #e1e6ec;
  background: #fafbfc;
}

/* ===== 弹窗 / 抽屉 ===== */
.dialog-tip {
  margin-top: 8px;
  color: var(--text-muted);
  font-size: 12px;
}

.chunk-body {
  min-height: 200px;
}

.chunk-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 14px;
  color: var(--text-muted);
  font-size: 13px;
}

.chunk-images {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 8px;
  margin-bottom: 16px;
}

.chunk-images .el-image {
  height: 90px;
  border: 1px solid #d3dae2;
  border-radius: 8px;
  cursor: zoom-in;
}

.chunk + .chunk {
  margin-top: 12px;
}

.chunk-idx {
  display: block;
  margin-bottom: 4px;
  color: var(--text-muted);
  font-size: 12px;
}

.chunk-text {
  margin: 0;
  padding: 10px 12px;
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid #d3dae2;
  border-radius: 8px;
  background: #f7f9fb;
  color: var(--text);
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 860px) {
  .kb-body {
    flex-direction: column;
    overflow-y: auto;
  }

  .kb-side {
    width: 100%;
    max-height: 220px;
    border-right: 0;
    border-bottom: 1px solid #d3dae2;
  }

  .kb-main {
    overflow: visible;
    padding: 16px;
  }

  .drop-zone {
    flex-wrap: wrap;
  }
}
</style>
