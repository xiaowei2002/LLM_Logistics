async function parse(res) {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `请求失败（${res.status}）`)
  return body
}

export async function optimizeScheduling(payload) {
  return parse(
    await fetch('/api/scheduling-offloading/optimize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  )
}

export async function analyzeSchedulingResult(result) {
  // 只发送解释需要的结构化摘要，不重复调用优化接口或上传完整决策轨迹。
  const { status, run_id, scenario, metrics, warnings, elapsed_seconds, decision_summary } = result
  const summary = decision_summary ? { ...decision_summary, by_action: (decision_summary.by_action || []).slice(0, 12) } : {}
  return parse(await fetch('/api/scheduling-offloading/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_result: { status, run_id, scenario, metrics, warnings, elapsed_seconds, decision_summary: summary } }),
  }))
}

export async function chatScheduling(query, threadId = null) {
  return parse(
    await fetch('/api/scheduling-offloading/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, thread_id: threadId }),
    }),
  )
}


export async function resumeScheduling(threadId, answer) {
  return parse(
    await fetch('/api/scheduling-offloading/resume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadId, answer }),
    }),
  )
}

export async function fetchSchedulingCapabilities() {
  return parse(await fetch('/api/scheduling-offloading/capabilities'))
}
