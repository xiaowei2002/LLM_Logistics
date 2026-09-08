/** Markdown 渲染：模型回复 → HTML（含防注入清洗） */
import { marked } from 'marked'
import DOMPurify from 'dompurify'

// 单个换行也生效（聊天场景），不用空行分段
marked.setOptions({ breaks: true, gfm: true })

export function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text))
}
