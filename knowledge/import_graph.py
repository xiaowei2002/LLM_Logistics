"""将 knowledge/merged_graph.json 导入 Neo4j。

数据形态：entities=实体名列表，relations=[主体, 关系, 客体] 三元组，edges=关系类型表。
图模型：(:Entity {name}) -[:RELATES {rel_type}]-> (:Entity)
会先清空库中全部旧数据，可重复执行（幂等）。连接参数读 backend/.env。
"""
import json
import os
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


def main():
    with open(Path(__file__).parent / "merged_graph.json", encoding="utf-8") as f:
        data = json.load(f)
    entities = sorted(set(data["entities"]))
    relations = [r for r in data["relations"] if isinstance(r, list) and len(r) == 3]

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
