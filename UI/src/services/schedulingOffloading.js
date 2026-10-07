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

export async function chatScheduling(query) {
  return parse(
    await fetch('/api/scheduling-offloading/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    }),
  )
}

export async function fetchSchedulingCapabilities() {
  return parse(await fetch('/api/scheduling-offloading/capabilities'))
}
