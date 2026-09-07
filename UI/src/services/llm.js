/** 大模型流式调用（前后端分离：由 FastAPI 后端封装并代理到智谱） */

/**
 * 流式对话：每收到一段增量就回调 onDelta({ content, reasoning })
 * @param {Array<{role: string, content: string}>} messages 完整对话历史
 * @param {({content?: string, reasoning?: string}) => void} onDelta 增量回调
 * @param {AbortSignal} [signal] 中断信号
 * @param {{deepThink?: boolean}} [options] deepThink 控制是否开启深度推理
 */
export async function streamChat(messages, onDelta, signal, { deepThink = true } = {}) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ messages, deepThink }),
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
        if (!payload) continue
        if (payload === '[DONE]') return

        let data
        try {
          data = JSON.parse(payload)
        } catch {
          continue // 忽略无法解析的片段
        }
        if (data.error) throw new Error(data.error)
        if (data.content || data.reasoning) {
          onDelta({ content: data.content, reasoning: data.reasoning })
        }
      }
    }
  }
}
