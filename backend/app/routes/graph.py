"""知识图谱只读查询接口：数据存于 Neo4j，本模块不做任何写操作。"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from neo4j import GraphDatabase

router = APIRouter(prefix="/graph", tags=["graph"])

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD")
        if not password:
            raise HTTPException(status_code=500, detail="后端未配置 NEO4J_PASSWORD")
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def _graph_payload(session, names):
    """给定节点名集合，返回其中的节点（含度数）与它们之间的所有边。"""
    nodes = session.run(
        """
        MATCH (n:Entity) WHERE n.name IN $names
        OPTIONAL MATCH (n)-[r]-()
        RETURN n.name AS name, count(r) AS degree
        ORDER BY degree DESC
        """,
        names=list(names),
    ).data()
    links = session.run(
        """
        MATCH (a:Entity)-[r:RELATES]->(b:Entity)
        WHERE a.name IN $names AND b.name IN $names
        RETURN a.name AS source, b.name AS target, r.rel_type AS relType
        """,
        names=list(names),
    ).data()
    return nodes, links


@router.get("/overview")
async def overview(limit: int = Query(150, ge=10, le=1067)) -> dict:
    """全图概览：默认返回度数最高的 150 个节点及其间关系。"""
    try:
        with _get_driver().session() as s:
            total_nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            total_links = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            top = [
                rec["name"]
                for rec in s.run(
                    """
                    MATCH (n:Entity)
                    OPTIONAL MATCH (n)-[r]-()
                    WITH n, count(r) AS degree
                    ORDER BY degree DESC LIMIT $limit
                    RETURN n.name AS name
                    """,
                    limit=limit,
                )
            ]
            nodes, links = _graph_payload(s, top)
        return {
            "totalNodes": total_nodes,
            "totalLinks": total_links,
            "nodes": nodes,
            "links": links,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Neo4j 查询失败: {exc}") from exc


@router.get("/stats")
async def stats() -> dict:
    """侧边栏基本信息：关系类型排行（实体/关系总数由 overview 返回）。"""
    try:
        with _get_driver().session() as s:
            top_rel_types = s.run(
                """
                MATCH ()-[r:RELATES]->()
                RETURN r.rel_type AS type, count(*) AS count
                ORDER BY count DESC LIMIT 10
                """
            ).data()
        return {"topRelTypes": top_rel_types}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Neo4j 查询失败: {exc}") from exc


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=50),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """按关键词搜索实体：返回匹配节点、一跳邻居及它们之间的边。"""
    try:
        with _get_driver().session() as s:
            matched = [
                rec["name"]
                for rec in s.run(
                    """
                    MATCH (n:Entity) WHERE n.name CONTAINS $q
                    WITH n ORDER BY n.name LIMIT $limit
                    RETURN n.name AS name
                    """,
                    q=q,
                    limit=limit,
                )
            ]
            if not matched:
                return {"matched": [], "nodes": [], "links": []}
            neighbors = {
                rec["name"]
                for rec in s.run(
                    """
                    MATCH (n:Entity)-[]-(m:Entity)
                    WHERE n.name IN $names
                    RETURN DISTINCT m.name AS name
                    """,
                    names=matched,
                )
            }
            nodes, links = _graph_payload(s, set(matched) | neighbors)
        return {"matched": matched, "nodes": nodes, "links": links}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Neo4j 查询失败: {exc}") from exc
