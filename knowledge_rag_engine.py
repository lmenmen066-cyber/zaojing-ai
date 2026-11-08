"""
造境 AI 设计智能体 - Knowledge Base / RAG Engine（真实实现）
============================================================

对应任务书 §65（知识库：PDF/DOCX/TXT/MD/HTML/Images + RAG）、§66（知识权限：
Global / Workspace / Project / Brand / Private）。

真实链路（无 Mock）：
1. 文档解析：md/txt 直读；html 去标签；docx 用 zipfile 解 word/document.xml 去标签；
   pdf 尝试 pypdf/pdfminer，缺依赖时诚实返回 extraction_failed（绝不编造正文）。
2. 分块：按窗口 + 重叠切段，保证上下文连续。
3. 向量化：复用素材引擎同款确定性 SHA-256 token-hash 投影（跨进程/重启可复现，
   规避 hash() 随机化与旧的零向量缺陷）。
4. 检索：query 向量化后按余弦相似度排序，支持作用域过滤，回传命中片段。
5. 持久化：条目落盘 JSON，重启可加载。

版本：V1.0
"""

from __future__ import annotations

import re
import json
import hashlib
import zipfile
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# 允许的作用域（§66）
class KnowledgeScope(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    PROJECT = "project"
    BRAND = "brand"
    PRIVATE = "private"


# ---------------------------------------------------------------------------
# 确定性文本向量（与 asset_engine.embed_text 同算法，保证向量空间一致）
# ---------------------------------------------------------------------------

def embed_text(text: str, dims: int = 512) -> List[float]:
    vec = [0.0] * dims
    tokens = re.findall(r"[a-z0-9一-鿿]+", (text or "").lower())
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % dims
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[idx] += sign
    if text:
        h = hashlib.sha256(("::" + text.lower()).encode("utf-8")).digest()
        vec[int.from_bytes(h[:4], "big") % dims] += (1.0 if (h[4] & 1) else -1.0)
    return vec


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# 真实文本抽取（按类型分派；缺依赖时诚实失败，不伪造正文）
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\f\v]+")


def _strip_html(html: str) -> str:
    # 去 script/style 内容，再去标签，实体粗解
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = _TAG_RE.sub(" ", html)
    for ent, ch in {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'"}.items():
        text = text.replace(ent, ch)
    text = _WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    # 段落结束换行，其余去标签
    xml = xml.replace("</w:p>", "\n")
    text = _TAG_RE.sub("", xml)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _extract_pdf(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """返回 (text, error)。优先 pypdf，次选 PyPDF2/pdfminer；都缺则 error 非空。"""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            return None, "pdf_extraction_unavailable (需要 pypdf/PyPDF2，未随包提供)"
    try:
        reader = PdfReader(str(path))
        parts = []
        for pg in reader.pages:
            try:
                parts.append(pg.extract_text() or "")
            except Exception as e:  # 单页失败不影响整体
                logger.warning(f"pdf page extract failed: {e}")
        text = "\n".join(parts).strip()
        return (text, None) if text else (None, "pdf_has_no_extractable_text")
    except Exception as e:
        return None, f"pdf_parse_error: {e}"


def extract_text(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    按扩展名分派抽取纯文本。返回 (text, error)，两者之一非空。
    图片等无文本类型 → error='no_text_layer'，交上层标注（不做 OCR）。
    """
    suffix = path.suffix.lower()
    try:
        if suffix in (".txt", ".md", ".markdown", ".rst", ".csv"):
            return path.read_text(encoding="utf-8", errors="replace"), None
        if suffix in (".html", ".htm", ".xhtml"):
            return _strip_html(path.read_text(encoding="utf-8", errors="replace")), None
        if suffix == ".docx":
            return _extract_docx(path), None
        if suffix == ".pdf":
            return _extract_pdf(path)
        if suffix in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".psd", ".ai"):
            return None, "no_text_layer (图像/矢量文档 OCR 需专用引擎，未实现)"
        # 未知类型：尝试按文本读，读不出可打印文本则失败
        raw = path.read_bytes()
        try:
            txt = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, f"unsupported_binary_type ({suffix})"
        printable = sum(1 for c in txt[:2000] if c.isprintable() or c in "\n\r\t")
        if txt[:2000] and printable / max(1, len(txt[:2000])) > 0.8:
            return txt, None
        return None, f"unsupported_type ({suffix})"
    except Exception as e:
        return None, f"extract_error: {e}"


def chunk_text(text: str, window: int = 600, overlap: int = 120) -> List[str]:
    """按字符窗口 + 重叠分块（对中英混排按字符计，简单稳健）。"""
    text = (text or "").strip()
    if not text:
        return []
    if window <= 0 or overlap >= window:
        overlap = window // 5
    chunks: List[str] = []
    step = max(1, window - overlap)
    for start in range(0, len(text), step):
        piece = text[start:start + window]
        if piece.strip():
            chunks.append(piece.strip())
        if start + window >= len(text):
            break
    return chunks


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeChunk:
    index: int
    text: str
    vector: List[float]


@dataclass
class KnowledgeItem:
    id: str
    title: str
    scope: str                       # KnowledgeScope 值
    source: str                      # 原始文件路径或 URL
    status: str                      # indexed / extraction_failed
    error: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    project_id: Optional[str] = None
    brand: Optional[str] = None
    workspace_id: Optional[str] = None
    char_count: int = 0
    created_at: Optional[datetime] = None
    chunks: List[KnowledgeChunk] = field(default_factory=list)

    def to_public_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "title": self.title,
            "scope": self.scope,
            "source": self.source,
            "status": self.status,
            "error": self.error,
            "tags": self.tags,
            "project_id": self.project_id,
            "brand": self.brand,
            "workspace_id": self.workspace_id,
            "char_count": self.char_count,
            "chunk_count": len(self.chunks),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        return d


# ---------------------------------------------------------------------------
# 知识库引擎
# ---------------------------------------------------------------------------

class KnowledgeEngine:
    """文档知识库 + RAG 检索引擎（真实落盘）。"""

    def __init__(self, store_dir: Path, window: int = 600, overlap: int = 120):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.window = window
        self.overlap = overlap
        self.items: Dict[str, KnowledgeItem] = {}
        self._load()

    # ---- 持久化 ----
    def _path(self, item_id: str) -> Path:
        return self.store_dir / f"{item_id}.json"

    def _load(self):
        for f in self.store_dir.glob("kb_*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                created = data.pop("created_at", None)
                chunks = [KnowledgeChunk(**c) for c in data.pop("chunks", [])]
                item = KnowledgeItem(**data)
                item.created_at = datetime.fromisoformat(created) if created else datetime.now()
                item.chunks = chunks
                self.items[item.id] = item
            except Exception as e:
                logger.error(f"Failed to load knowledge {f}: {e}")

    def _save(self, item: KnowledgeItem):
        data = {
            "id": item.id, "title": item.title, "scope": item.scope, "source": item.source,
            "status": item.status, "error": item.error, "tags": item.tags,
            "project_id": item.project_id, "brand": item.brand, "workspace_id": item.workspace_id,
            "char_count": item.char_count,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "chunks": [{"index": c.index, "text": c.text, "vector": c.vector} for c in item.chunks],
        }
        self._path(item.id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 摄入 ----
    def ingest_file(self, file_path: str, *, title: Optional[str] = None,
                    scope: str = "project", tags: Optional[List[str]] = None,
                    project_id: Optional[str] = None, brand: Optional[str] = None,
                    workspace_id: Optional[str] = None, item_id: Optional[str] = None) -> KnowledgeItem:
        p = Path(file_path)
        item_id = item_id or ("kb_" + hashlib.sha256(str(p).encode()).hexdigest()[:12])
        text, error = extract_text(p) if p.is_file() else (None, "file_not_found")

        item = KnowledgeItem(
            id=item_id,
            title=title or p.name,
            scope=scope,
            source=str(p),
            status="indexed" if text else "extraction_failed",
            error=error,
            tags=tags or [],
            project_id=project_id,
            brand=brand,
            workspace_id=workspace_id,
            created_at=datetime.now(),
        )

        if text:
            item.char_count = len(text)
            for i, ch in enumerate(chunk_text(text, self.window, self.overlap)):
                item.chunks.append(KnowledgeChunk(index=i, text=ch, vector=embed_text(ch)))

        self.items[item_id] = item
        self._save(item)
        logger.info(f"Knowledge ingest {item_id}: {item.status} chunks={len(item.chunks)}")
        return item

    def ingest_text(self, text: str, *, title: str, scope: str = "project",
                    tags: Optional[List[str]] = None, item_id: Optional[str] = None,
                    **ids) -> KnowledgeItem:
        item_id = item_id or ("kb_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12])
        item = KnowledgeItem(
            id=item_id, title=title, scope=scope, source="inline",
            status="indexed" if text.strip() else "empty",
            tags=tags or [], created_at=datetime.now(),
            **{k: v for k, v in ids.items() if k in ("project_id", "brand", "workspace_id")},
        )
        item.char_count = len(text)
        for i, ch in enumerate(chunk_text(text, self.window, self.overlap)):
            item.chunks.append(KnowledgeChunk(index=i, text=ch, vector=embed_text(ch)))
        self.items[item_id] = item
        self._save(item)
        return item

    # ---- 作用域过滤（§66：Agent 只能调用有权限的知识）----
    def _visible(self, item: KnowledgeItem, allowed_scopes: Optional[List[str]],
                 context: Optional[Dict[str, Any]]) -> bool:
        if allowed_scopes and item.scope not in allowed_scopes:
            return False
        ctx = context or {}
        if item.scope == KnowledgeScope.PROJECT.value and ctx.get("project_id"):
            return item.project_id in (None, ctx["project_id"])
        if item.scope == KnowledgeScope.BRAND.value and ctx.get("brand"):
            return item.brand in (None, ctx["brand"])
        if item.scope == KnowledgeScope.WORKSPACE.value and ctx.get("workspace_id"):
            return item.workspace_id in (None, ctx["workspace_id"])
        return True

    # ---- 检索 ----
    def retrieve(self, query: str, *, top_k: int = 5,
                 allowed_scopes: Optional[List[str]] = None,
                 context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        qvec = embed_text(query)
        scored: List[Tuple[float, KnowledgeItem, KnowledgeChunk]] = []
        for item in self.items.values():
            if item.status != "indexed" or not self._visible(item, allowed_scopes, context):
                continue
            for ch in item.chunks:
                scored.append((cosine(qvec, ch.vector), item, ch))
        scored.sort(key=lambda t: t[0], reverse=True)
        out = []
        for score, item, ch in scored[:top_k]:
            out.append({
                "knowledge_id": item.id,
                "title": item.title,
                "scope": item.scope,
                "chunk_index": ch.index,
                "similarity": round(score, 6),
                "snippet": ch.text[:400],
            })
        return out

    def list_items(self, allowed_scopes: Optional[List[str]] = None,
                   context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return [
            it.to_public_dict() for it in self.items.values()
            if (not allowed_scopes or it.scope in allowed_scopes)
            and (it.scope not in (KnowledgeScope.PROJECT.value,) or self._visible(it, allowed_scopes, context))
        ]

    def get_item(self, item_id: str) -> Optional[KnowledgeItem]:
        return self.items.get(item_id)

    def delete_item(self, item_id: str) -> bool:
        if item_id in self.items:
            self.items.pop(item_id)
            f = self._path(item_id)
            try:
                f.unlink()
            except Exception:
                pass
            return True
        return False


if __name__ == "__main__":
    import tempfile  # noqa
    print("=" * 60)
    print("Knowledge RAG - 断言自测（会因伪造打分/桩检索失败）")
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp())
    (tmp / "brand.txt").write_text("品牌主色为茶叶绿，logo 需留白空间，禁止拉伸变形。" * 30, encoding="utf-8")
    store = tmp / "kb"
    eng = KnowledgeEngine(store)

    # 测试 1: 真实摄入 —— indexed + 分块 + 字符数 + 向量已算
    it = eng.ingest_file(str(tmp / "brand.txt"), title="茶品牌规范", scope="brand", brand="acme")
    assert it.status == "indexed", ("摄入应成功 indexed", it.status, it.error)
    assert len(it.chunks) >= 1 and it.char_count > 0, ("应产出分块与字符数", len(it.chunks), it.char_count)
    assert it.chunks[0].vector and len(it.chunks[0].vector) == 512, "分块须带 512 维向量"
    print(f"  [OK] ingest: status=indexed chunks={len(it.chunks)} chars={it.char_count}")

    # 测试 2: 真实落盘 + 重启可加载（持久化非内存假象）
    kb_files = list(store.glob("kb_*.json"))
    assert kb_files, "条目必须落盘为 kb_*.json"
    eng2 = KnowledgeEngine(store)  # 新实例从磁盘重载
    assert it.id in eng2.items and eng2.items[it.id].chunks, "重载后应恢复条目与分块"
    print(f"  [OK] persistence: {len(kb_files)} 落盘文件 + 重载命中条目")

    # 测试 3: 确定性向量 —— 同串两次一致、自身余弦≈1（可复现，非随机/零向量缺陷）
    v1, v2 = embed_text("logo 留白"), embed_text("logo 留白")
    assert v1 == v2, "embed_text 必须确定可复现"
    assert abs(cosine(v1, v2) - 1.0) < 1e-9, "自身余弦应为 1"
    assert any(x != 0.0 for x in v1), "向量不得恒为全零（旧缺陷）"
    print("  [OK] embed 确定性 + 非零向量")

    # 测试 4: 相关性判别（核心反伪造）—— 命中查询的相似度必须严格高于无关查询
    rel = eng.retrieve("logo 留白 拉伸", allowed_scopes=["brand"], context={"brand": "acme"}, top_k=3)
    irr = eng.retrieve("quantum chromodynamics banana helicopter", allowed_scopes=["brand"], context={"brand": "acme"}, top_k=3)
    assert rel and rel[0]["similarity"] > 0.1, ("相关查询应高相似命中", rel[:1])
    assert "logo" in rel[0]["snippet"].lower() or "留白" in rel[0]["snippet"] or "拉伸" in rel[0]["snippet"], \
        ("命中片段应真含查询内容", rel[0]["snippet"][:80])
    irr_top = irr[0]["similarity"] if irr else 0.0
    assert rel[0]["similarity"] - irr_top > 0.05, \
        ("相关须显著高于无关（若打分伪造/恒定则失配）", rel[0]["similarity"], irr_top)
    print(f"  [OK] 相关性判别: rel={rel[0]['similarity']} >> irr={irr_top}")

    # 测试 5: §66 作用域隔离 —— 他品牌/错作用域检索必须返回空（真实可见性门控）
    other = eng.retrieve("logo", allowed_scopes=["brand"], context={"brand": "OTHER"}, top_k=5)
    assert other == [], ("acme 品牌知识不得泄漏给 OTHER 品牌", other)
    wrongscope = eng.retrieve("logo", allowed_scopes=["project"], context={"brand": "acme"}, top_k=5)
    assert wrongscope == [], ("brand 条目不应在仅允许 project 作用域时可见", wrongscope)
    print("  [OK] 作用域隔离: 他品牌/错作用域均诚实返回空")

    print("\n" + "=" * 60)
    print("[PASS] Knowledge RAG 断言自测全部通过（真实落盘+相关性判别+作用域隔离）")
    print("=" * 60)
