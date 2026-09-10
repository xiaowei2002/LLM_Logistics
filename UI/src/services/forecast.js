/* 需求预测接口封装：智能体预测 + 人在环（Human-in-the-loop）续答 */

async function parse(res) {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `请求失败（${res.status}）`)
  return body
}

/* 发起预测：query 为自然语言请求；同一线程续问时传 threadId */
export async function predictDemand(query, threadId = null) {
  return parse(
    await fetch('/api/demand-forecast/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, thread_id: threadId }),
    }),
  )
}

/* 人在环续答：把用户对澄清问题的回答发回，智能体从断点继续 */
export async function resumeDemand(threadId, answer) {
  return parse(
    await fetch('/api/demand-forecast/resume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadId, answer }),
    }),
  )
}
