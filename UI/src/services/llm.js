/** 大模型流式调用（开发测试用：前端直连智谱，后续切换到后端接口） */

const API_KEY = import.meta.env.VITE_LLM_API_KEY
const MODEL = import.meta.env.VITE_LLM_MODEL || 'glm-5.2'

export function isLLMConfigured() {
  return Boolean(API_KEY)
}

/**
 * 流式对话：每收到一段增量就回调 onDelta({ content, reasoning })
 * @param {Array<{role: string, content: string}>} messages 完整对话历史
 * @param {({content?: string, reasoning?: string}) => void} onDelta 增量回调
 * @param {AbortSignal} [signal] 中断信号
 * @param {{deepThink?: boolean}} [options] deepThink 控制是否开启深度推理
 */
export async function streamChat(messages, onDelta, signal, { deepThink = true } = {}) {
  const res = await fetch('/llm/api/paas/v4/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${API_KEY}`,
    },
    body: JSON.stringify({
      model: MODEL,
      messages,
      stream: true,
      thinking: { type: deepThink ? 'enabled' : 'disabled' },
    }),
    signal,
  })

  if (!res.ok) {
    throw new Error(`接口返回 ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split('\n\n')
    buffer = events.pop() // 最后一段可能不完整，留到下一轮

    for (const event of events) {
      for (const line of event.split('\n')) {
        if (!line.startsWith('data:')) continue
        const payload = line.slice(5).trim()
        if (payload === '[DONE]') return
        try {
          const delta = JSON.parse(payload).choices?.[0]?.delta
          if (delta?.content || delta?.reasoning_content) {
            onDelta({ content: delta?.content, reasoning: delta?.reasoning_content })
          }
        } catch {
          // 忽略无法解析的片段
        }
      }
    }
  }
}
