<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Opportunity, Paperclip, PictureFilled, Promotion } from '@element-plus/icons-vue'
// 注：此处使用 PictureFilled（实心图片图标），小尺寸下更清晰

import IconAgent from '@/components/icons/IconAgent.vue'

const props = defineProps({
  modeText: {
    type: String,
    default: '物流任务',
  },
  hint: {
    type: String,
    default: '有什么物流任务需要我完成？智能体将自动规划并执行。',
  },
  deepThink: {
    type: Boolean,
    default: false,
  },
  /* 外部传入的草稿（编辑历史消息时填入） */
  draft: {
    type: String,
    default: '',
  },
  /* 紧凑模式：聊天底部输入条，单行起、自动长高 */
  compact: {
    type: Boolean,
    default: false,
  },
  /* 是否正在生成回复：发送键变停止键 */
  streaming: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['send', 'toggle-deep', 'stop'])

const content = ref('')
const inputRef = ref(null)

/* 输入框高度自适应（不用 el-input 的 autosize：其缩回后 Chrome 常残留滚动条）
   每次内容变化重算：低于上限时 overflow hidden，滚动条必然消失；超上限才出现滚动条 */
const MAX_INPUT_HEIGHT = 200

async function autoResize() {
  await nextTick()
  const el = inputRef.value?.textarea
  if (!el) return
  el.style.height = 'auto'
  const capped = Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)
  el.style.height = `${capped}px`
  el.style.overflowY = el.scrollHeight > MAX_INPUT_HEIGHT ? 'auto' : 'hidden'
}

watch(content, autoResize)
onMounted(() => {
  autoResize()
  /* 直接绑在内部 textarea 上：el-input 对 paste 的事件透传不可靠 */
  inputRef.value?.textarea?.addEventListener('paste', onPaste)
})

/* ===== 图片上传 ===== */
const MAX_IMAGES = 9
const images = ref([]) // 待发送图片（压缩后的 base64 data URL）
const fileRef = ref(null)

function pickImages() {
  fileRef.value?.click()
}


function onFilesChange(event) {
  const files = [...event.target.files]
  event.target.value = '' // 清空选择，允许重复选同一张
  for (const file of files) addImage(file)
}

/* 单张图片（选择与粘贴共用）：压缩后加入待发送列表 */
function addImage(file) {
  if (!file.type.startsWith('image/')) return
  if (images.value.length >= MAX_IMAGES) {
    ElMessage.warning(`最多上传 ${MAX_IMAGES} 张图片`)
    return
  }
  compressImage(file).then((url) => {
    if (images.value.length < MAX_IMAGES) {
      images.value.push({ id: Date.now() + Math.random(), url })
    }
  })
}

/* Ctrl+V 粘贴：图片直接进预览条，文档走解析接口，文本交给默认行为 */
async function onPaste(event) {
  const data = event.clipboardData
  if (!data) return
  // 优先取 items（截图/网页复制），否则取 files（资源管理器里 Ctrl+C 的文件）
  let files = [...(data.items || [])]
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile())
    .filter(Boolean)
  if (!files.length) files = [...(data.files || [])]
  files = files.filter((f, i, arr) => arr.findIndex((x) => x.name === f.name && x.size === f.size) === i)
  if (!files.length) return
  event.preventDefault()
  for (const file of files) {
    if (file.type.startsWith('image/')) addImage(file)
  }
}

/* 拖拽悬停状态：计数器避免鼠标在子元素间移动时闪烁 */
const dragDepth = ref(0)
const dragOver = computed(() => dragDepth.value > 0)

/* 拖拽文件进输入框：图片进预览条，其他文件走文档解析 */
function onDrop(event) {
  dragDepth.value = 0
  const files = [...(event.dataTransfer?.files || [])]
  if (!files.length) return
  for (const file of files) {
    if (file.type.startsWith('image/')) addImage(file)
  }
}

function removeImage(id) {
  images.value = images.value.filter((img) => img.id !== id)
}

/* 图片压缩：最长边 1280px、JPEG 85%，避免 base64 过大拖慢请求 */
function compressImage(file) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const objectUrl = URL.createObjectURL(file)
    img.onload = () => {
      URL.revokeObjectURL(objectUrl)
      const scale = Math.min(1, 1280 / Math.max(img.width, img.height))
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(img.width * scale)
      canvas.height = Math.round(img.height * scale)
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = '#fff' // PNG 透明底补白，避免转 JPEG 后变黑
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      resolve(canvas.toDataURL('image/jpeg', 0.85))
    }
    img.onerror = reject
    img.src = objectUrl
  })
}

/* 外部草稿变化时填入输入框（编辑历史消息） */
watch(
  () => props.draft,
  (v) => {
    if (v) {
      content.value = v
      inputRef.value?.focus()
    }
  },
)

function handleSend() {
  const text = content.value.trim()
  if (!text && !images.value.length) {
    inputRef.value?.focus()
    return
  }

  emit('send', text, images.value.map((img) => img.url))
  content.value = ''
  images.value = []
}

function handleEnter(event) {
  if (event.isComposing) return
  event.preventDefault()
  handleSend()
}

</script>

<template>
  <div class="input-wrap">
    <div
      class="input-box"
      :class="{ dragging: dragOver }"
      @dragenter.prevent="dragDepth++"
      @dragleave="dragDepth = Math.max(0, dragDepth - 1)"
      @dragover.prevent
      @drop.prevent="onDrop"
    >
      <!-- 拖拽悬停提示浮层 -->
      <div v-if="dragOver" class="drop-overlay">
        <el-icon :size="24"><Paperclip /></el-icon>
        <span>松开鼠标，上传文件</span>
      </div>
      <div class="input-top">
        <el-tag class="mode-chip" effect="plain">
          <IconAgent />
          <span>{{ modeText }}</span>
        </el-tag>
        <span class="hint">{{ hint }}</span>
      </div>

      <!-- 已选图片预览条 -->
      <div v-if="images.length" class="image-strip">
        <div v-for="img in images" :key="img.id" class="image-chip">
          <img :src="img.url" alt="" />
          <button class="image-remove" type="button" @click="removeImage(img.id)">×</button>
        </div>
      </div>

      <el-input
        ref="inputRef"
        v-model="content"
        class="task-input"
        :class="{ compact }"
        type="textarea"
        :rows="compact ? 1 : 3"
        resize="none"
        placeholder="例如：预测下季度整车运输需求，并给出生产调度建议…"
        @keydown.enter.exact="handleEnter"
      />

      <div class="input-bottom">
        <div class="bottom-left">
          <el-tooltip content="上传图片（最多 9 张）" placement="top" :show-after="300">
            <el-button class="attach-btn" circle @click="pickImages">
              <el-icon><PictureFilled /></el-icon>
            </el-button>
          </el-tooltip>
          <el-button
            class="deep-btn"
            :type="deepThink ? 'primary' : 'default'"
            round
            @click="emit('toggle-deep')"
          >
            <el-icon><Opportunity /></el-icon>
            <span>深度思考</span>
          </el-button>
          <span class="send-hint">Enter 发送，Shift + Enter 换行</span>
        </div>
        <el-button
          class="send-btn"
          type="primary"
          circle
          @click="streaming ? emit('stop') : handleSend()"
        >
          <span v-if="streaming" class="stop-square" />
          <el-icon v-else><Promotion /></el-icon>
        </el-button>
      </div>

      <!-- 隐藏的文件选择框：只允许图片、可多选 -->
      <input
        ref="fileRef"
        type="file"
        accept="image/*"
        multiple
        hidden
        @change="onFilesChange"
      />
    </div>
  </div>
</template>

<style scoped>
.input-wrap {
  max-width: 760px;
  margin: 0 auto;
  width: 100%;
}

.input-box {
  position: relative;
  border: 2px solid transparent;
  border-radius: 16px;
  background:
    linear-gradient(var(--bg), var(--bg)) padding-box,
    linear-gradient(90deg, #ff8bd2, #b57bee, #5b8def) border-box;
  box-shadow: var(--shadow);
  padding: 16px 20px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 拖拽悬停：高亮边框 + 覆盖提示层 */
.input-box.dragging {
  border-color: var(--primary);
}

.drop-overlay {
  position: absolute;
  inset: 2px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  color: var(--primary);
  font-size: 13px;
  pointer-events: none;
}

.input-top {
  display: flex;
  align-items: center;
  gap: 10px;
}

.mode-chip {
  background: var(--fill-light);
  border-color: transparent;
  color: var(--text);
  font-weight: 600;
  font-size: 13px;
  line-height: 1;
  flex-shrink: 0;
  height: auto;
  padding: 6px 12px;
  border-radius: 8px;
}

/* el-tag 内部有一层 .el-tag__content 包裹图标和文字，对齐/间距要设在这一层才生效 */
.mode-chip :deep(.el-tag__content) {
  display: inline-flex;
  align-items: flex-end; /* 图标与文字底边对齐 */
  gap: 7px;
}

.mode-chip :deep(svg) {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  margin-bottom: 2px; /* 补偿中文字形底部的下沉空间，视觉底边齐平 */
  color: var(--primary);
}

.hint {
  color: var(--text-muted);
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 去掉 el-input 默认边框，融入渐变外框 */
.task-input :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  background: transparent;
  resize: none;
  overflow-y: hidden; /* 初始隐藏；是否出现滚动条由 autoResize 按高度动态控制 */
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
  padding: 2px 4px;
}

/* 内部滚动条：细滚动条，与聊天区一致 */
.task-input :deep(.el-textarea__inner)::-webkit-scrollbar {
  width: 6px;
}

.task-input :deep(.el-textarea__inner)::-webkit-scrollbar-thumb {
  background: #d3d7de;
  border-radius: 3px;
}

.task-input :deep(.el-textarea__inner)::placeholder {
  color: #c3c9d2;
}

/* 紧凑模式：单行起、自动长高，聊天底部输入条形态 */
.task-input.compact :deep(.el-textarea__inner) {
  min-height: 26px;
}

.input-box:has(.task-input.compact) {
  padding: 12px 18px 10px;
  gap: 6px;
}

/* ===== 图片预览条 ===== */
.image-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.image-chip {
  position: relative;
  width: 60px;
  height: 60px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--border);
}

.image-chip img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.image-remove {
  position: absolute;
  top: 0;
  right: 0;
  width: 18px;
  height: 18px;
  border: none;
  border-radius: 0 0 0 8px;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  font-size: 13px;
  line-height: 18px;
  cursor: pointer;
  padding: 0;
}

.image-remove:hover {
  background: rgba(0, 0, 0, 0.65);
}

/* 上传按钮：圆形描边框 */
.attach-btn {
  width: 32px;
  height: 32px;
  padding: 0;
  color: var(--text-muted);
  border: 1px solid var(--border);
  background: transparent;
}

.attach-btn:hover {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--fill-light);
}

.input-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.bottom-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* 抵消 el-button 相邻默认 margin，间距统一由上面的 gap 控制 */
.bottom-left :deep(.el-button + .el-button) {
  margin-left: 0;
}

.deep-btn {
  font-size: 13px;
}

.send-hint {
  color: var(--text-placeholder);
  font-size: 12px;
  margin-left: 6px; /* 与深度思考按钮拉开一点，避免过挤 */
}

.send-btn {
  width: 32px;
  height: 32px;
  padding: 0;
}

/* 停止键图标：蓝底圆内的白色小方块（通用 AI 停止样式） */
.stop-square {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  background: #fff;
}

@media (max-width: 620px) {
  .send-hint {
    display: none;
  }
}
</style>
