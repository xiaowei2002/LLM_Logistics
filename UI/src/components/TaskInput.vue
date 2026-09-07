<script setup>
import { computed, ref, watch } from 'vue'
import { Opportunity, Promotion } from '@element-plus/icons-vue'

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
})

const emit = defineEmits(['send', 'toggle-deep'])

const content = ref('')
const inputRef = ref(null)

/* el-input 自动伸缩行数：紧凑模式单行起，非紧凑三行起 */
const autosize = computed(() =>
  props.compact ? { minRows: 1, maxRows: 6 } : { minRows: 3, maxRows: 8 },
)

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
  if (!text) {
    inputRef.value?.focus()
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
        <el-tag class="mode-chip" effect="plain">
          <IconAgent />
          <span>{{ modeText }}</span>
        </el-tag>
        <span class="hint">{{ hint }}</span>
      </div>

      <el-input
        ref="inputRef"
        v-model="content"
        class="task-input"
        :class="{ compact }"
        type="textarea"
        :autosize="autosize"
        resize="none"
        placeholder="例如：预测下季度整车运输需求，并给出生产调度建议…"
        @keydown.enter.exact="handleEnter"
      />

      <div class="input-bottom">
        <div class="bottom-left">
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
        <el-button class="send-btn" type="primary" circle @click="handleSend">
          <el-icon><Promotion /></el-icon>
        </el-button>
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
  border-color: transparent;
  color: var(--text);
  font-weight: 600;
  font-size: 13px;
  flex-shrink: 0;
  height: auto;
  padding: 6px 12px;
  border-radius: 8px;
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

/* 去掉 el-input 默认边框，融入渐变外框 */
.task-input :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  background: transparent;
  resize: none;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
  padding: 2px 4px;
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
  font-size: 13px;
}

.send-hint {
  color: var(--text-placeholder);
  font-size: 12px;
}

.send-btn {
  width: 32px;
  height: 32px;
  padding: 0;
}

@media (max-width: 620px) {
  .send-hint {
    display: none;
  }
}
</style>
