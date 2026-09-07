<script setup>
import { ref } from 'vue'

import IconAgent from '@/components/icons/IconAgent.vue'
import IconSend from '@/components/icons/IconSend.vue'

defineProps({
  modeText: {
    type: String,
    default: '物流任务',
  },
  hint: {
    type: String,
    default: '有什么物流任务需要我完成？智能体将自动规划并执行。',
  },
})

const emit = defineEmits(['send'])

const content = ref('')
const textareaRef = ref(null)

function handleSend() {
  const text = content.value.trim()
  if (!text) {
    textareaRef.value?.focus()
    return
  }

  emit('send', text)
  content.value = ''
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
        rows="3"
        placeholder="例如：预测下季度整车运输需求，并给出生产调度建议…"
        @keydown.enter.exact="handleEnter"
      ></textarea>

      <div class="input-bottom">
        <span class="send-hint">Enter 发送，Shift + Enter 换行</span>
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
  border-radius: var(--radius-lg);
  background:
    linear-gradient(var(--bg), var(--bg)) padding-box,
    linear-gradient(90deg, #ff8bd2, #b57bee, #5b8def) border-box;
  box-shadow: var(--shadow);
  padding: 16px 18px 12px;
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
  color: var(--text);
  background: transparent;
}

.task-input::placeholder {
  color: var(--text-placeholder);
}

.input-bottom {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
}

.send-hint {
  color: var(--text-placeholder);
  font-size: 12px;
}

.send-btn {
  width: 36px;
  height: 36px;
  border: none;
  border-radius: var(--radius-md);
  background: var(--primary);
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.send-btn:hover {
  opacity: 0.88;
}

@media (max-width: 620px) {
  .send-hint {
    display: none;
  }
}
</style>
