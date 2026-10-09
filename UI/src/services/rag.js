/* 知识库（RAG）接口封装：走 Vite 代理 /rag → RAG 服务（独立进程，端口 8000） */

async function parse(res) {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `请求失败（${res.status}）`)
  return body
}

/* 支持的文件类型，如 ['.docx', '.pdf', '.txt'] */
export async function fetchFormats() {
  return parse(await fetch('/rag/formats'))
}

/* 文档列表（仅元数据：文件名 / 大小 / 状态 / 分块数 / 时间） */
export async function fetchDocuments() {
  return parse(await fetch('/rag/documents'))
}

/* 上传文件：后端落盘后立即返回 task_id，解析在后台进行 */
export async function uploadDocument(file) {
  const form = new FormData()
  form.append('file', file)
  return parse(await fetch('/rag/documents', { method: 'POST', body: form }))
}

/* 单个文档的解析进度与分块内容 */
export async function fetchTask(taskId) {
  return parse(await fetch(`/rag/tasks/${encodeURIComponent(taskId)}`))
}

/* 删除文档：原文件 + 分块结果 + 记录一并清除 */
export async function deleteDocument(taskId) {
  return parse(
    await fetch(`/rag/documents/${encodeURIComponent(taskId)}`, { method: 'DELETE' }),
  )
}
