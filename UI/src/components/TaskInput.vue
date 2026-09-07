<script setup>
import { nextTick, ref, watch } from 'vue'

import IconAgent from '@/components/icons/IconAgent.vue'
import IconSend from '@/components/icons/IconSend.vue'

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
})

watch(
  () => props.draft,
  (v) => {
    if (v) {
      content.value = v
      textareaRef.value?.focus()
    }
  },
)

/* 自动伸缩：高度跟随内容，最多 160px；清空后完全复位 */
function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  if (el.value) {
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  } else {
    el.style.height = ''
  }
}

const emit = defineEmits(['send', 'toggle-deep'])

const content = ref('')
const textareaRef = ref(null)

/* 程序性清空（如发送后）不触发 input 事件，这里兜底 */
watch(content, () => {
  nextTick(autoResize)
})

function handleSend() {
  const text = content.value.trim()
  if (!text) {
    textareaRef.value?.focus()
    return
  }

  emit('send', text)
  content.value = ''
  nextTick(autoResize)
}

function handleEnter(event) {
  if (event.isComposing) return
  event.preventDefault()
  handleSend()
}
</script>

<template>
  <div class="input-wrap">
    <div class="input-box">
      <div class="input-top">
        <span class="mode-chip">
          <IconAgent />
          <span>{{ modeText }}</span>
        </span>
        <span class="hint">{{ hint }}</span>
      </div>

      <textarea
        ref="textareaRef"
        v-model="content"
        class="task-input"
        :class="{ compact }"
        :rows="compact ? 1 : 3"
        placeholder="例如：预测下季度整车运输需求，并给出生产调度建议…"
        @keydown.enter.exact="handleEnter"
        @input="autoResize"
      ></textarea>

      <div class="input-bottom">
        <div class="bottom-left">
          <button
            class="deep-btn"
            :class="{ active: deepThink }"
            type="button"
            title="开启后模型会先深度推理再回答"
            @click="emit('toggle-deep')"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3a6 6 0 0 0-4 10.5c.6.5 1 1.5 1 2.5h6c0-1 .4-2 1-2.5A6 6 0 0 0 12 3z"/><line x1="10" y1="20" x2="14" y2="20"/></svg>
            深度思考
          </button>
          <span class="send-hint">Enter 发送，Shift + Enter 换行</span>
        </div>
        <button class="send-btn" type="button" title="发送" @click="handleSend">
          <IconSend />
        </button>
      </div>
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

.input-top {
  display: flex;
  align-items: center;
  gap: 10px;
}

.mode-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--fill-light);
  border-radius: 8px;
  padding: 6px 12px;
  font-weight: 600;
  font-size: 13px;
  flex-shrink: 0;
}

.mode-chip :deep(svg) {
  color: var(--primary);
}

.hint {
  color: var(--text-muted);
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-input {
  border: none;
  outline: none;
  resize: none;
  font-size: 14px;
  font-family: inherit;
  min-height: 72px;
  line-height: 1.6;
  color: var(--text);
  background: transparent;
  padding: 2px 4px;
}

.task-input::placeholder {
  color: #c3c9d2;
}

/* 紧凑模式：单行起、自动长高，聊天底部输入条形态 */
.task-input.compact {
  min-height: 26px;
  max-height: 160px;
  overflow-y: auto;
}

.input-box:has(.task-input.compact) {
  padding: 12px 18px 10px;
  gap: 6px;
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
  gap: 12px;
}

.deep-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text-muted);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.deep-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
}

.deep-btn.active {
  background: var(--primary-light);
  border-color: transparent;
  color: var(--primary);
  font-weight: 600;
}

.send-hint {
  color: var(--text-placeholder);
  font-size: 12px;
}

.send-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: var(--primary);
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}

.send-btn:hover {
  background: #245bd0;
}

@media (max-width: 620px) {
  .send-hint {
    display: none;
  }
}
</style>
