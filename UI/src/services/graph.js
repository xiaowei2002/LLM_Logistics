/* 知识图谱接口封装：后端 Neo4j 只读查询 */

async function parse(res) {
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `请求失败（${res.status}）`)
  return body
}

/* 全图概览：返回度数最高的 limit 个节点及其间关系 */
export async function fetchOverview(limit = 150) {
  return parse(await fetch(`/api/graph/overview?limit=${limit}`))
}

/* 关键词搜索：返回匹配实体、一跳邻居及其间关系 */
export async function searchGraph(keyword, limit = 10) {
  return parse(await fetch(`/api/graph/search?q=${encodeURIComponent(keyword)}&limit=${limit}`))
}

/* 侧边栏统计：关系类型排行 */
export async function fetchStats() {
  return parse(await fetch('/api/graph/stats'))
}
