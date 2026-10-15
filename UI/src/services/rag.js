/* 多模态知识库（joyagent mrag）接口封装：走 Vite 代理 /rag → RAG 服务（独立进程，端口 8000） */

const BASE = '/rag/v1'

async function parse(res) {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || body.message || `请求失败（${res.status}）`)
  if (body.code !== undefined && body.code !== 200) throw new Error(body.msg || '请求失败')
  return body.data ?? {}
}

async function post(path, payload) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parse(res)
}

/* 后端签发的存储链接是 http://127.0.0.1:8000/storage/...，改走前端代理，换机器访问也能显示 */
export function toProxyUrl(url) {
  return (url || '').replace(/^https?:\/\/(?:127\.0\.0\.1|localhost):8000(?=\/)/, '/rag')
}

/* ===== 知识库 ===== */
export async function listKnowledgeBases() {
  const data = await post('/documents/list_knowledge_base', { page_no: 1, page_size: 100 })
  return data.list || []
}

export function createKnowledgeBase({ name, desc }) {
  return post('/documents/create_knowledge_base', { kb_id: '', kb_name: name, kb_desc: desc })
}

export function deleteKnowledgeBase(kbId) {
  return post('/documents/delete_knowledge_base', { kb_id: kbId })
}

/* ===== 文件 ===== */
export function listKbFiles(kbId, pageNo, pageSize) {
  return post('/documents/list_kb_files', { kb_id: kbId, page_no: pageNo, page_size: pageSize })
}

/* 上传到存储并拿到文件链接（尚未入库） */
export async function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  return parse(await fetch(`${BASE}/documents/upload`, { method: 'POST', body: form }))
}

/* 把已上传的文件加入知识库，后台异步解析、切分、向量化 */
export function addFiles(kbId, files) {
  return post('/documents/add_files', { kb_id: kbId, files })
}

export function addWebUrl(kbId, url) {
  return post('/documents/add_web_url', { kb_id: kbId, url })
}

export function deleteFiles(kbId, fileIds) {
  return post('/documents/delete_files', { kb_id: kbId, file_ids: fileIds })
}

/* 单个文件的文本分块与解析出的图片 */
export function listFileChunks(kbId, fileId) {
  return post('/documents/list_file_chunks', { kb_id: kbId, file_id: fileId })
}

/* ===== 检索问答：Agentic RAG，SSE 逐块返回 OpenAI 格式的 chunk ===== */
export async function streamQuery({ kbId, question }, onDelta, signal) {
  const res = await fetch(`${BASE}/tool/mragQuery`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kb_id: kbId, question, image_urls: [] }),
    signal,
  })
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `请求失败（${res.status}）`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split(/\r?\n/)
    buffer = lines.pop()
    for (const line of lines) {
      if (!line.startsWith('data:')) continue
      const data = line.slice(5).trim()
      if (!data || data === '[DONE]') continue
      try {
        const content = JSON.parse(data).choices?.[0]?.delta?.content
        if (content) onDelta(content)
      } catch {
        /* 非 JSON 的心跳等行直接忽略 */
      }
    }
  }
}
