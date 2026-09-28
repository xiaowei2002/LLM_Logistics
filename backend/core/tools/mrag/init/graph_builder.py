"""
==========================================================================
知识图谱构建：把 chunk 展平成全文，按大块做【两阶段抽取】生成 merged_graph.json

  chunk 展平 → 16384 字符/块 → 每块【两阶段抽取】
    ① 抽实体（entities.txt 提示词，15 类实体，只保留 name）
    ② 抽关系（relations.txt 提示词，主宾强制落在实体列表内）
  → 合并所有块 → semhash 语义去重（嵌入余弦 ≥ 阈值归并）→ 写 merged_graph.json

全程 llm_cache.json 磁盘缓存，重跑命中缓存 = 零 LLM 调用。
输出格式与 storage/graph_store.py 的 load_graph 完全对齐：
  {"entities": [...], "relations": [["实体A", "关系", "实体B"], ...]}
==========================================================================
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.tools.mrag.utils.utils import config, logger
from core.tools.mrag.utils.llm import chat

# 单分块实体数硬上限：防止过度抽取导致关系输入与去重成本爆炸。
MAX_ENTITIES_PER_CHUNK = 150


# ==========================================================================
# 提示词
# ==========================================================================
_KG_ENTITY_PROMPT = """你将从物流行业文档中抽取实体，用于构建知识图谱。请识别文档中提到的重要实体，这些实体应可能与其他实体存在有意义的关系，并以去重后的列表形式返回。

## 实体类别

分析文档时，请从以下类别中为每个实体选择最合适的类型。如果某个实体确实重要但无法归入现有类别，可标注为"其他"并给出建议类别：

- **组织**：承运商、货主、物流公司、供应链企业、监管机构、行业协会、政府部门、标准化组织
- **标准/文件**：国家标准（GB/T）、行业标准（WB/T）、国际标准（ISO）、法规、技术规范、合同、证书、手册
- **术语**：物流行业专有名词、专业定义、缩写/简称的全称
- **业务活动**：运输、仓储、配送、报关、装卸、包装、流通加工、多式联运、集货、分拣
- **运输方式**：公路运输、铁路运输、海运、空运、内河运输、管道运输、冷链运输、危险品运输、多式联运
- **指标/参数**：温度、湿度、时效、载重、尺寸、体积、油耗、破损率、准点率、完好率、KPI、服务水平
- **地点**：港口、码头、仓库、配送中心、口岸、物流园区、中转站、货运站、枢纽城市、经济区域
- **设备/器具**：运输车辆、集装箱、托盘、周转箱、叉车、货架、冷链设备、RFID标签、包装材料、搬运工具
- **岗位/角色**：调度员、仓管员、配送员、报关员、质检员、司机、运营经理、安全员
- **信息系统**：WMS（仓储管理系统）、TMS（运输管理系统）、ERP、OMS、GPS追踪、EDI
- **人员**：标准起草人、行业专家、学者、企业负责人、历史人物
- **时间**：具体日期、年份、时间段、有效期、过渡期
- **事件**：行业会议、政策发布、标准实施、重大事故、历史里程碑
- **物料/商品**：汽车零部件、整车、散货、危险品、冷链品、原材料、成品
- **法规条款**：法律条文、行政处罚规定、强制性要求、免责条款、责任界定

## 抽取准则

请遵循以下规则识别实体：

1. **关注实质性提及**：抽取对文档主题至关重要的实体，或出现时有明确细节与上下文的实体。仅一笔带过且没有上下文的实体应跳过。

2. **优先选择有关系潜力的实体**：由于这些实体将用于构建知识图谱，只纳入可能在文档中与其他实体存在明确关系的实体。与文档主线叙事无关的孤立实体通常应排除。

3. **使用官方或标准名称**：对每个实体使用文档中出现的最广泛认可或最正式的名称。标准文件应保留完整编号。

4. **避免过于泛化的词汇**：不要抽取"运输"、"物流"等过于泛化的词作为实体，除非它在文档中作为特定概念被明确定义。

5. **规范化实体名称**：如果同一实体在文档中有多种名称或变体，选择规范形式。例如，全称和简称统一为文档中首次出现的正式名称；"GB/T"和"推荐性国家标准"统一为具体标准编号。

6. **每个唯一实体只抽取一次**：即使一个实体出现多次，在最终输出中只列出一次。

7. **宁缺毋滥**：当不确定某个实体是否满足条件时，宁可排除也不要纳入。一个聚焦的、关系紧密的实体集合，优于一个包含大量孤立实体的全面列表。

8. **保留编号与层级**：对于标准文件中的条目编号（如"3.1.2"、"A.4"），保留其完整编号与层级信息，以便后续关联。

## 输出格式

以 JSON 对象格式输出，包含 entities 数组，每个实体包含 name 和 type 字段：

{"entities": [{"name": "实体名称", "type": "实体类别"}]}

严格要求：
- 只输出 JSON 对象，不要任何解释文字
- type 应优先从上述 15 个类别中选择
- 如实体确实无法归入现有类别，type 填"其他"
- 实体名称应简洁准确，保留关键编号"""

_KG_RELATION_PROMPT = """你将从给定文本中抽取主语-谓词-宾语三元组，用于构建知识图谱。一份实体列表已从此文本中预先抽取，你的任务是识别这些实体之间的关系。

你需要抽取格式为 (主语 | 谓词 | 宾语) 的三元组：
- **主语** 必须是实体列表中的实体
- **宾语** 必须是实体列表中的实体
- **谓词** 描述主语和宾语之间的关系

## 谓词类型参考

物流行业常见关系类型如下，请优先使用，但也可以根据实际文本灵活补充更精确的谓词：

- **定义为**：术语与其定义之间的关系。如（整车物流 | 定义为 | 以整车为物流服务对象的业务活动）
- **引用**：标准/文件之间的引用关系。如（GB/T 31152-2014 | 引用 | GB/T 18354-2021）
- **适用于**：标准/规范与适用对象之间的关系。如（GB/T 31149-2014 | 适用于 | 汽车物流服务）
- **要求**：主体对指标/参数的强制或推荐性要求。如（冷链运输 | 要求 | 温度控制）
- **属于**：子概念与父概念之间的上下位关系。如（城市配送 | 属于 | 配送）
- **发生于**：业务活动发生的地点。如（报关 | 发生于 | 口岸）
- **由…执行**：业务活动与执行组织之间的关系。如（质量检验 | 由…执行 | 质检员）
- **使用**：业务活动或组织所使用的设备/器具/系统。如（仓储管理 | 使用 | WMS）
- **包含**：整体与部分之间的关系。如（供应链 | 包含 | 物流）
- **运输方式**：业务活动所采用的运输方式。如（汽车零部件运输 | 运输方式 | 公路运输）
- **规定**：标准/法规对某事项的规定。如（GB/T 22126-2025 | 规定 | 物流中心作业规范）
- **负责**：岗位/角色所承担的职责。如（仓管员 | 负责 | 库存盘点）

## 抽取准则

**准确性与忠实性：**
- 只抽取文本中明确陈述或清晰隐含的关系
- 不要推断超出文本范围的关系
- 只抽取事实性关系（非观点、假设或否定陈述）

**谓词质量：**
- 使用清晰、具体的谓词精准描述关系
- 优先从上述物流行业常见谓词中选择
- 如果现有谓词无法准确描述，可创建更合适的新谓词
- 避免使用模糊谓词如"相关于"、"关联到"，除非确实无法确定更具体的关系

**方向性：**
- 确保主语-谓词-宾语的顺序正确表达了关系的方向
- 例如：（GB/T 31149-2014 | 适用于 | 汽车物流服务），而非（汽车物流服务 | 适用于 | GB/T 31149-2014）

**充分性：**
- 抽取文本中存在的所有有意义的关系
- 特别关注那些可能没有连接的实体——如果某个实体出现在实体列表中，它应当至少有一个关系
- 目标是最大化知识图谱的连通性，最小化孤立实体

## 输出格式

以 JSON 对象格式输出，包含 relations 字段，每个关系包含 subject、predicate、object 字段：

{"relations": [{"subject": "主语实体名称", "predicate": "谓词", "object": "宾语实体名称"}]}

严格要求：
- 只输出 JSON 对象，不要任何解释文字
- subject 和 object 必须精确匹配提供的实体列表中的实体名称
- predicate 应尽可能从参考谓词类型中选择，也可灵活补充
- 每个关系应能独立验证，不依赖其他关系的上下文"""


# ==========================================================================
# 工具：JSON 解析 / 分块 / 缓存 / 去重
# ==========================================================================
def _parse_json(text: str) -> Any:
    """从 LLM 响应中解析 JSON，容错 ``` 围栏、解释文字、前后缀。"""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return decoder.raw_decode(text[i:])[0]
            except Exception:  # noqa: BLE001
                continue
    return None


def _parse_entities(raw: str) -> List[str]:
    """解析实体抽取响应：只保留 name，去重保序。"""
    data = _parse_json(raw)
    if isinstance(data, dict):
        data = data.get("entities", [])
    if not isinstance(data, list):
        return []
    names: List[str] = []
    seen = set()
    for item in data:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name") or item.get("entity")
        else:
            continue
        if isinstance(name, str):
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _parse_relations(raw: str, entities: List[str]) -> List[Tuple[str, str, str]]:
    """解析关系抽取响应，丢弃主宾不在实体列表内的三元组。"""
    entities_set = set(entities)
    data = _parse_json(raw)
    if isinstance(data, dict):
        data = data.get("relations", [])
    if not isinstance(data, list):
        return []
    rels: List[Tuple[str, str, str]] = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        s, p, o = item.get("subject"), item.get("predicate"), item.get("object")
        if not (isinstance(s, str) and isinstance(p, str) and isinstance(o, str)):
            continue
        s, p, o = s.strip(), p.strip(), o.strip()
        if not (s and p and o):
            continue
        if s not in entities_set or o not in entities_set:
            continue
        t = (s, p, o)
        if t not in seen:
            seen.add(t)
            rels.append(t)
    return rels


# ---- 分块（对齐师兄 chunk_text.py：句子边界优先，单句超长回退字符/词切分） ----
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；!?;])\s*|\n+")


def _split_sentences(text: str) -> List[str]:
    parts = _SENTENCE_BOUNDARY.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _split_long_sentence(sentence: str, max_chunk_size: int) -> List[str]:
    if not sentence:
        return []
    if len(sentence) <= max_chunk_size:
        return [sentence]
    if " " in sentence:
        units, sep = sentence.split(), " "
    else:
        units, sep = list(sentence), ""
    chunks: List[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else current + sep + unit
        if len(candidate) <= max_chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(unit) > max_chunk_size:
                chunks.extend(unit[i : i + max_chunk_size] for i in range(0, len(unit), max_chunk_size))
                current = ""
            else:
                current = unit
    if current:
        chunks.append(current)
    return chunks


def _chunk_text(text: str, max_chunk_size: int) -> List[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chunk_size:
        return [text]
    sentences = _split_sentences(text)
    chunks: List[str] = []
    current_chunk = ""
    for sentence in sentences:
        if len(sentence) > max_chunk_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            chunks.extend(_split_long_sentence(sentence, max_chunk_size))
            continue
        if len(current_chunk) + len(sentence) + 1 <= max_chunk_size:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks


# ---- 磁盘缓存（对齐师兄 llm_cache.py：内容哈希键，命中零 LLM 调用） ----
def _cache_key(step: str, model: str, *parts: str) -> str:
    h = hashlib.sha256()
    h.update(step.encode("utf-8"))
    for p in (model,) + parts:
        h.update(b"\x00")
        h.update(p.encode("utf-8"))
    return h.hexdigest()


class _LLMCache:
    def __init__(self, path: Optional[Path]):
        self.path = path
        self.hits = 0
        self.data: Dict[str, Any] = {}
        self._lock = threading.Lock()
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                logger.warning("图谱 LLM 缓存文件损坏，已忽略: {}", path)
                self.data = {}

    def get(self, step: str, model: str, *parts: str):
        if self.path is None:
            return None
        key = _cache_key(step, model, *parts)
        with self._lock:
            if key in self.data:
                self.hits += 1
                return self.data[key]
        return None

    def set(self, step: str, model: str, value: Any, *parts: str) -> None:
        if self.path is None:
            return
        with self._lock:
            self.data[_cache_key(step, model, *parts)] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")


# ---- 语义去重（semhash：嵌入余弦 ≥ 阈值归并到先出现的代表名） ----
def _semhash_dedup(
    names: List[str], embedder: Any, threshold: float
) -> Tuple[List[str], Dict[str, str]]:
    """把语义近似的实体名归并，返回 (去重后列表, {原始名 -> 代表名})。"""
    names = list(names)
    if len(names) <= 1:
        return names, {n: n for n in names}

    norm = [unicodedata.normalize("NFKC", n).strip() for n in names]
    vecs = np.asarray(embedder.embed_texts(norm), dtype=np.float32)
    vecs = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
    sims = vecs @ vecs.T

    parent = list(range(len(names)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if sims[i, j] >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri

    reps: Dict[int, str] = {}
    for i, n in enumerate(names):
        reps.setdefault(find(i), n)

    deduped: List[str] = []
    seen = set()
    for i, n in enumerate(names):
        rep = reps[find(i)]
        if rep not in seen:
            seen.add(rep)
            deduped.append(rep)

    canon = {n: reps[find(i)] for i, n in enumerate(names)}
    return deduped, canon


# ==========================================================================
# 抽取（走 utils/llm.chat，temperature=0 保证稳定）
# ==========================================================================
def _extract_entities(text: str) -> List[str]:
    msgs = [
        {"role": "system", "content": _KG_ENTITY_PROMPT},
        {"role": "user", "content": f"以下是要从中提取实体的文本：\n<article>\n{text}\n</article>"},
    ]
    return _parse_entities(chat(msgs, temperature=0.0))


def _extract_relations(text: str, entities: List[str]) -> List[Tuple[str, str, str]]:
    entities_str = "\n".join(f"- {e}" for e in entities)
    msgs = [
        {"role": "system", "content": _KG_RELATION_PROMPT},
        {
            "role": "user",
            "content": (
                "以下是从源文本中先前提取到的实体列表：\n"
                f"<entities>\n{entities_str}\n</entities>\n\n"
                "以下是待分析的源文本：\n"
                f"<text>\n{text}\n</text>"
            ),
        },
    ]
    return _parse_relations(chat(msgs, temperature=0.0), entities)


# ==========================================================================
# 主流程
# ==========================================================================
def _flatten_chunks(chunks: List[Dict[str, Any]]) -> str:
    """把 chunk 列表展平成带章节路径的全文。"""
    parts: List[str] = []
    for c in chunks:
        content = (c.get("content") or "").strip()
        if not content:
            continue
        sp = (c.get("section_path") or "").strip()
        if sp:
            parts.append(f"# {sp}")
        parts.append(content)
    return "\n\n".join(parts)


def build_graph(
    chunks: List[Dict[str, Any]],
    extract_entities_fn: Any = None,
    extract_relations_fn: Any = None,
    embedder: Any = None,
    chunk_size: Optional[int] = None,
    max_workers: Optional[int] = None,
    threshold: Optional[float] = None,
    merge_existing: bool = True,
) -> Dict[str, Any]:
    """从 chunk 列表构建知识图谱，写 merged_graph.json。

    Args:
        chunks: loader 输出的 chunk 列表（含 content / section_path）
        extract_entities_fn / extract_relations_fn: 注入点（自测用），缺省走 qwen
        embedder: 去重用的嵌入器，缺省 get_embedder()
        chunk_size: 抽取分块字符数，缺省 config.kg_chunk_size
        max_workers: 并发线程数，缺省 ThreadPoolExecutor 默认（师兄做法）
        threshold: 语义去重相似度阈值，缺省 config.graph_dedup_threshold
        merge_existing: True 则读旧 merged_graph.json 合并（增量累积），False 则覆盖
    """
    extract_entities_fn = extract_entities_fn or _extract_entities
    extract_relations_fn = extract_relations_fn or _extract_relations
    chunk_size = chunk_size or config.kg_chunk_size
    threshold = threshold if threshold is not None else config.graph_dedup_threshold

    text = _flatten_chunks(chunks)
    if not text.strip():
        logger.warning("chunks 展平后为空，跳过图谱构建")
        return {"nodes": 0, "edges": 0, "path": str(config.graph_path)}

    blocks = _chunk_text(text, chunk_size)
    logger.info("图谱构建：展平 {} 字符 → {} 块（chunk_size={}）", len(text), len(blocks), chunk_size)

    cache = _LLMCache(config.graph_index_dir / "kg_llm_cache.json")
    model = config.llm_model

    entities: set = set()
    relations: set = set()

    def _extract(block: str) -> Tuple[set, set]:
        ents = cache.get("entities", model, block)
        if ents is None:
            ents = list(extract_entities_fn(block))[:MAX_ENTITIES_PER_CHUNK]
            cache.set("entities", model, ents, block)
        ents_sig = json.dumps(ents, ensure_ascii=False)
        rels = cache.get("relations", model, block, ents_sig)
        if rels is None:
            rels = extract_relations_fn(block, ents)
            cache.set("relations", model, [list(r) for r in rels], block, ents_sig)
        return set(ents), set(tuple(r) for r in rels)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_extract, b): b for b in blocks}
        for fut in as_completed(futures):
            ents, rels = fut.result()
            entities.update(ents)
            relations.update(rels)

    logger.info(
        "图谱抽取完成（缓存命中 {} 次）：{} 实体 / {} 关系（去重前）",
        cache.hits, len(entities), len(relations),
    )

    # 语义去重 + 关系里的实体名映射回代表名
    if not entities:
        entities_list, canon = [], {}
    else:
        if embedder is None:
            from core.tools.mrag.embedding.embedding import get_embedder

            embedder = get_embedder()
        entities_list, canon = _semhash_dedup(list(entities), embedder, threshold)

    rels = set()
    for s, p, o in relations:
        s2, o2 = canon.get(s, s), canon.get(o, o)
        if s2 != o2:  # 去掉自环
            rels.add((s2, p, o2))

    # 增量：读旧图合并（实体保序精确去重 + 关系集合并），rebuild 时跳过
    if merge_existing and config.graph_path.is_file():
        try:
            old = json.loads(config.graph_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = {}
        old_entities = [e for e in old.get("entities", []) if isinstance(e, str)]
        old_relations = {
            tuple(r) for r in old.get("relations", [])
            if isinstance(r, list) and len(r) == 3 and all(isinstance(x, str) for x in r)
        }
        entities_list = list(dict.fromkeys(old_entities + entities_list))
        rels = old_relations | rels

    data = {"entities": entities_list, "relations": [list(r) for r in sorted(rels)]}
    config.graph_path.parent.mkdir(parents=True, exist_ok=True)
    config.graph_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("图谱已写入 {}：{} 实体 / {} 关系（去重后）", config.graph_path, len(entities_list), len(rels))
    return {"nodes": len(entities_list), "edges": len(rels), "path": str(config.graph_path)}


if __name__ == "__main__":
    # 命令行自测（假抽取 + 假 embedder，不联网不下模型）：
    #   cd backend && python -m core.tools.mrag.init.graph_builder
    class FakeEmbedder:
        def embed_texts(self, texts):
            table = {
                "整车物流": [1.0, 0.0, 0.0, 0.0],
                "整车运输": [0.999, 0.0, 0.0, 0.0],  # 与"整车物流"余弦≈0.999
                "仓储管理": [0.0, 1.0, 0.0, 0.0],
                "汽车物流": [0.0, 0.0, 1.0, 0.0],
                "托盘": [0.0, 0.0, 0.0, 1.0],
            }
            return [table.get(t, [0.0, 0.0, 0.0, 0.0]) for t in texts]

    # 1. 单元：JSON 容错解析
    raw_ents = '```json\n{"entities": [{"name": "整车物流", "type": "业务活动"}, {"name": "GB/T 18354", "type": "标准"}]}\n```'
    assert _parse_entities(raw_ents) == ["整车物流", "GB/T 18354"], "实体解析不对"

    raw_rels = '{"relations": [{"subject": "整车物流", "predicate": "属于", "object": "汽车物流"}, {"subject": "不存在", "predicate": "属于", "object": "汽车物流"}]}'
    assert _parse_relations(raw_rels, ["整车物流", "汽车物流"]) == [("整车物流", "属于", "汽车物流")], "关系解析/过滤不对"

    # 2. 单元：分块（句子边界）
    blocks = _chunk_text("第一句。第二句！第三句？", 6)
    assert len(blocks) == 3, f"分块不对: {blocks}"

    # 3. 单元：语义去重（"整车物流"≈"整车运输"，其余独立）
    deduped, canon = _semhash_dedup(["整车物流", "整车运输", "仓储管理"], FakeEmbedder(), 0.95)
    assert deduped == ["整车物流", "仓储管理"], f"去重不对: {deduped}"
    assert canon["整车运输"] == "整车物流", "canonical 映射不对"

    # 4. 端到端：build_graph 写 merged_graph.json（假抽取，小 chunk_size 强制多块并发）
    def fake_entities(text):
        return [n for n in ("整车物流", "汽车物流", "仓储管理", "托盘") if n in text]

    def fake_relations(text, entities):
        rels = []
        if "整车物流" in entities and "汽车物流" in entities:
            rels.append(("整车物流", "属于", "汽车物流"))
        if "仓储管理" in entities and "托盘" in entities:
            rels.append(("仓储管理", "使用", "托盘"))
        return rels

    chunks = [
        {"content": "整车物流是指以整车为服务对象的业务活动。", "section_path": "第一章"},
        {"content": "整车运输成本受燃油影响。", "section_path": "第一章"},
        {"content": "仓储管理需要使用托盘完成入库。", "section_path": "第二章"},
    ]
    _old_graph_path = config.graph_path
    config.graph_path = Path(config.output_dir) / "_demo_merged_graph.json"
    try:
        result = build_graph(
            chunks,
            extract_entities_fn=fake_entities,
            extract_relations_fn=fake_relations,
            embedder=FakeEmbedder(),
            chunk_size=12,
            max_workers=2,
            threshold=0.95,
        )
        data = json.loads(config.graph_path.read_text(encoding="utf-8"))
        print("entities:", data["entities"])
        print("relations:", data["relations"])
        assert "整车物流" in data["entities"]
        assert ["仓储管理", "使用", "托盘"] in data["relations"]
        assert result["nodes"] > 0 and result["edges"] > 0
    finally:
        config.graph_path = _old_graph_path

    print("ALL_PASS")
