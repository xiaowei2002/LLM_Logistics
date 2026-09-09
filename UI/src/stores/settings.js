/** LLM 配置：存浏览器 localStorage，随每次聊天请求作为 config 参数传给后端 */
import { ref } from 'vue'
import { defineStore } from 'pinia'

const STORAGE_KEY = 'llm-logistics-llm-config'

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch {
    // 存档损坏时忽略，回退默认
  }
  return null
}

export const useSettingsStore = defineStore('settings', () => {
  const saved = load()
  const config = ref({
    model: saved?.model ?? '',
    base_url: saved?.base_url ?? '',
    api_key: saved?.api_key ?? '',
    temperature: saved?.temperature ?? null,
    max_tokens: saved?.max_tokens ?? null,
    top_p: saved?.top_p ?? null,
  })

  function save(next) {
    config.value = { ...config.value, ...next }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(config.value))
    } catch {
      // 存储失败不阻塞保存
    }
  }

  /* 导出给 chat 请求的参数：剔除空值，让后端回退 .env 默认 */
  function toParams() {
    const c = config.value
    const params = {}
    if (c.model) params.model = c.model
    if (c.base_url) params.base_url = c.base_url
    if (c.temperature != null) params.temperature = c.temperature
    if (c.max_tokens != null) params.max_tokens = c.max_tokens
    if (c.top_p != null) params.top_p = c.top_p
    return params
  }

  return { config, save, toParams }
})
