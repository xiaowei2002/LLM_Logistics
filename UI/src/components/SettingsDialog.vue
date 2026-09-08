<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Setting } from '@element-plus/icons-vue'

const props = defineProps({
  /* 对话框开关：v-model:open */
  open: {
    type: Boolean,
    default: false,
  },
})
const emit = defineEmits(['update:open'])

const form = reactive({
  model: '',
  base_url: '',
  api_key: '',
  temperature: null,
  max_tokens: null,
  top_p: null,
})
const loading = ref(false)
const saving = ref(false)

/* 打开时从后端加载当前配置 */
watch(
  () => props.open,
  async (v) => {
    if (!v) return
    loading.value = true
    try {
      const res = await fetch('/api/settings/llm')
      if (!res.ok) throw new Error(`接口返回 ${res.status}`)
      const data = await res.json()
      form.model = data.model ?? ''
      form.base_url = data.base_url ?? ''
      form.temperature = data.temperature ?? null
      form.max_tokens = data.max_tokens ?? null
      form.top_p = data.top_p ?? null
      form.api_key = data.api_key ?? ''
    } catch (err) {
      ElMessage.error(`读取配置失败：${err.message}`)
    } finally {
      loading.value = false
    }
  },
)

async function save() {
  if (saving.value) return
  if (!form.model.trim()) {
    ElMessage.warning('模型名称不能为空')
    return
  }
  saving.value = true
  try {
    const body = {
      model: form.model.trim(),
      base_url: form.base_url.trim(),
      // 采样参数留空（null）= 使用模型默认值
      temperature: form.temperature,
      max_tokens: form.max_tokens,
      top_p: form.top_p,
    }
    // API Key 不填 = 沿用当前已保存的 Key
    if (form.api_key.trim()) body.api_key = form.api_key.trim()

    const res = await fetch('/api/settings/llm', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const detail = (await res.json().catch(() => null))?.detail?.[0]?.msg
      throw new Error(detail || `接口返回 ${res.status}`)
    }
    ElMessage.success('配置已保存，即时生效')
    emit('update:open', false)
  } catch (err) {
    ElMessage.error(`保存失败：${err.message}`)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="open"
    width="560px"
    :close-on-click-modal="false"
    class="settings-dialog"
    @update:model-value="emit('update:open', $event)"
  >
    <!-- 标题区 -->
    <template #header>
      <div class="dialog-header">
        <div class="header-icon">
          <el-icon :size="20"><Setting /></el-icon>
        </div>
        <div class="header-text">
          <h3>系统设置</h3>
          <p>大模型配置，保存后即时生效</p>
        </div>
      </div>
    </template>

    <div v-loading="loading">
      <!-- 分组一：模型配置 -->
      <div class="section-title">模型配置</div>
      <el-form label-position="top" class="settings-form">
        <el-form-item label="模型名称">
          <el-input v-model="form.model" placeholder="例如：glm-4.5v / glm-5.2" />
        </el-form-item>
        <el-form-item label="API 地址">
          <el-input
            v-model="form.base_url"
            name="llm-base-url"
            autocomplete="off"
            placeholder="OpenAI 兼容接口地址"
          />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            name="llm-api-key"
            type="password"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
      </el-form>

      <!-- 分组二：生成参数 -->
      <div class="section-title">生成参数</div>
      <div class="params-grid">
        <div class="param-cell">
          <span class="param-label">温度</span>
          <el-input-number
            v-model="form.temperature"
            :min="0"
            :max="2"
            :step="0.1"
            controls-position="right"
            placeholder="默认"
            class="param-input"
          />
          <span class="field-hint">越高越随机</span>
        </div>
        <div class="param-cell">
          <span class="param-label">最大 Tokens</span>
          <el-input-number
            v-model="form.max_tokens"
            :min="1"
            :step="256"
            controls-position="right"
            placeholder="默认"
            class="param-input"
          />
          <span class="field-hint">回复长度上限</span>
        </div>
        <div class="param-cell">
          <span class="param-label">Top P</span>
          <el-input-number
            v-model="form.top_p"
            :min="0"
            :max="1"
            :step="0.05"
            controls-position="right"
            placeholder="默认"
            class="param-input"
          />
          <span class="field-hint">候选词范围</span>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="emit('update:open', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
/* ===== 标题区 ===== */
.dialog-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: linear-gradient(135deg, var(--primary), #7a9bff);
  flex-shrink: 0;
}

.header-text h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}

.header-text p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}

/* ===== 分组标题 ===== */
.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin: 4px 0 14px;
}

.section-title::before {
  content: '';
  width: 4px;
  height: 14px;
  border-radius: 2px;
  background: var(--primary);
}

/* ===== 表单 ===== */
.settings-form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.settings-form :deep(.el-form-item__label) {
  font-weight: 500;
  padding-bottom: 4px;
}

.field-hint {
  display: block;
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
  line-height: 1.4;
}

/* ===== 生成参数三列 ===== */
.params-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.param-cell {
  display: flex;
  flex-direction: column;
}

.param-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  margin-bottom: 6px;
}

.param-input {
  width: 100%;
}

/* ===== 对话框整体圆角 ===== */
:global(.settings-dialog) {
  border-radius: 16px;
}
</style>
