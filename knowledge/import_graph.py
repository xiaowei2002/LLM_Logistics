"""导入知识图谱 JSON 到 Neo4j。

用法：python knowledge/import_graph.py [数据文件.json]
不传参数时默认读同目录 merged_graph.json。

数据格式约定：
{
  "entities": ["实体1", "实体2", ...],           // 实体名列表
  "relations": [["主体", "关系", "客体"], ...]    // 三元组列表
}
图模型：(:Entity {name}) -[:RELATES {rel_type}]-> (:Entity)
会先清空库中全部旧数据，可重复执行（幂等）。连接参数读 backend/.env。
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / "backend" / ".env")

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD")
if not PASSWORD:
    raise SystemExit("请在 backend/.env 里配置 NEO4J_PASSWORD")

BATCH = 500


def chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def load_graph():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "merged_graph.json"
    if not path.exists():
        raise SystemExit(f"数据文件不存在: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entities = data.get("entities")
    relations = data.get("relations")
    if not isinstance(entities, list) or not isinstance(relations, list):
        raise SystemExit(
            f"{path.name} 格式不符合约定：需要顶层含 entities(实体名列表) "
            "和 relations([主体, 关系, 客体] 列表) 两个键，详见脚本头部说明"
        )
    return sorted(set(map(str, entities))), [
        [str(x) for x in r] for r in relations if isinstance(r, list) and len(r) == 3
    ]


def main():
    entities, relations = load_graph()

    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    with driver.session() as s:
        # 清空旧数据（本系统仅承载这一份知识图谱）
        s.run("MATCH (n) DETACH DELETE n")

        # 实体名唯一索引：MERGE 去重 + 查询加速
        s.run("CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)")

        for batch in chunks([{"name": n} for n in entities], BATCH):
            s.run(
                "UNWIND $rows AS row MERGE (:Entity {name: row.name})",
                rows=batch,
            )

        for batch in chunks(
            [{"src": r[0], "rel": r[1], "dst": r[2]} for r in relations], BATCH
        ):
            s.run(
                """
                UNWIND $rows AS row
                MATCH (a:Entity {name: row.src})
                MATCH (b:Entity {name: row.dst})
                MERGE (a)-[:RELATES {rel_type: row.rel}]->(b)
                """,
                rows=batch,
            )

        node_count = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rel_count = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        print(f"导入完成：节点 {node_count}，关系 {rel_count}")
        print(f"期望值  ：节点 {len(entities)}，关系 {len(relations)}")


if __name__ == "__main__":
    main()
