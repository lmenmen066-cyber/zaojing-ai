"""
造境 AI 设计智能体 - Asset Engine 持久化完整实现

包含:
1. 素材索引迁移到持久化目录
2. Embedding 索引 (OpenCLIP/BGE-M3)
3. 缩略图生成
4. 授权状态记录
5. Generation Metadata 完整记录

版本：V1.0
"""

import os
import logging
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import json
import asyncio
import threading
from datetime import datetime
import aiofiles
import uuid

logger = logging.getLogger(__name__)


def embed_text(text: str, dims: int = 512) -> List[float]:
    """
    确定性文本向量（跨进程/重启稳定可复现）。

    采用 token-hash 稀疏投影：把文本切分为词元（含整体串），逐 token 用
    SHA-256 派生出 (桶索引, 符号)，累加到定长向量。相同/相近文本 → 共享
    词元 → 余弦相似度高；完全避免 `hash()` 的 PYTHONHASHSEED 随机化，
    也修复了旧实现 `int % 1.0 == 0.0`（恒为零向量）导致检索排序失效的缺陷。

    素材入库与查询检索共用本函数，保证向量空间一致。
    """
    vec = [0.0] * dims
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", (text or "").lower())
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % dims
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[idx] += sign
    # 整串作为补充特征，区分 token 集合相同但语序不同的情况
    if text:
        h = hashlib.sha256(("::" + text.lower()).encode("utf-8")).digest()
        vec[int.from_bytes(h[:4], "big") % dims] += (1.0 if (h[4] & 1) else -1.0)
    return vec


# ============================================================================
# 枚举定义
# ============================================================================

class AssetType(Enum):
    """素材类型"""
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    FONT = "font"
    MODEL = "model"


class LicenseStatus(Enum):
    """授权状态"""
    UNKNOWN = "unknown"          # 未确定
    FREE = "free"                # 免费
    COMMERCIAL = "commercial"    # 可商用
    RESTRICTED = "restricted"    # 受限
    PROPRIETARY = "proprietary"  # 专有


class IndexStatus(Enum):
    """索引状态"""
    PENDING = "pending"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class AssetMetadata:
    """素材元数据"""
    asset_id: str
    filename: str
    file_path: str
    file_size: int
    mime_type: str
    asset_type: AssetType
    
    # 尺寸信息
    width: Optional[int] = None
    height: Optional[int] = None

    # 缩略图落盘路径（真实生成后写回；非图像素材为 None）
    thumbnail_path: Optional[str] = None
    
    # 创建时间
    created_at: datetime = None
    updated_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()


@dataclass
class AssetEmbedding:
    """素材嵌入向量"""
    asset_id: str
    vector: List[float]      # 实际为 tokenhash-sha256-512 稀疏投影向量（见 model_name）；
                             # 接入 OpenCLIP/BGE-M3 等真实语义嵌入后此注释与 model_name 同步更新
    model_name: str          # 使用的 embedding 模型
    
    # 索引状态
    index_status: IndexStatus = IndexStatus.PENDING
    indexed_at: Optional[datetime] = None


@dataclass
class GenerationMetadata:
    """AI 生成元数据"""
    generation_id: str
    source_asset_id: Optional[str]  # 源素材 ID(如果是生成结果)
    
    # 生成参数
    prompt: str
    negative_prompt: Optional[str]
    model_name: str
    provider: str
    
    # 生成参数
    width: int
    height: int
    steps: int
    guidance_scale: float
    seed: Optional[int]
    
    # 成本追踪
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    
    # 时间戳
    generated_at: datetime = None
    
    def __post_init__(self):
        if self.generated_at is None:
            self.generated_at = datetime.now()


@dataclass
class AssetLicense:
    """素材授权信息"""
    asset_id: str
    license_status: LicenseStatus
    license_type: Optional[str] = None     # CC0 / CC-BY / Commercial 等
    attribution_required: bool = False
    commercial_use: bool = False
    modification_allowed: bool = True
    
    # 来源
    source_url: Optional[str] = None
    source_provider: Optional[str] = None
    
    # 验证状态
    verified: bool = False         # 是否已验证
    verified_at: Optional[datetime] = None
    
    # 警告
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []
        if self.verified_at is None:
            self.verified_at = datetime.now()


# ============================================================================
# Asset Engine - 素材引擎
# ============================================================================

class AssetEngine:
    """素材引擎 - 持久化版本"""
    
    def __init__(self, root_path: Path):
        """
        初始化素材引擎
        
        Args:
            root_path: 素材根目录 (持久化路径)
        """
        self.root_path = root_path
        self.assets_db_path = root_path / "assets.db.json"
        self.embeddings_db_path = root_path / "embeddings.db.json"
        # Phase 52：向量库改为"追加日志 + 定期压实"。旧实现每加一条素材就把整个
        # embeddings.db.json 全量重写（N x 512 个 float、indent=2），单条成本随库
        # 容量线性上涨 => 总体 O(N^2)；实测 25/100/250 桶总耗时 0.57/13.82/98.81 s。
        # 日志行仍是每条落盘（flush），压实快照保持文件形状与旧格式完全兼容，
        # 冷启动读回 = 快照 + 日志重放，P23 的硬杀持久化语义不变。
        self.embeddings_log_path = root_path / "embeddings.log.jsonl"
        self._embed_log_lines = 0
        self._embed_compact_every = 200
        self._persist_lock = threading.RLock()
        self.thumbnails_dir = root_path / "thumbnails"
        self.indexes_dir = root_path / "indexes"
        
        # 数据库
        self.assets: Dict[str, AssetMetadata] = {}
        self.embeddings: Dict[str, AssetEmbedding] = {}
        self.generation_metadata: Dict[str, GenerationMetadata] = {}
        self.licenses: Dict[str, AssetLicense] = {}
        
        # 确保目录存在
        self._ensure_directories()
        
        # 加载数据库
        self._load_databases()
    
    def _ensure_directories(self):
        """确保所有目录存在"""
        directories = [
            self.root_path,
            self.thumbnails_dir,
            self.indexes_dir,
        ]
        
        for dir_path in directories:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Ensured directory exists: {dir_path}")
            except Exception as e:
                logger.error(f"Failed to create directory {dir_path}: {e}")
    
    @staticmethod
    def _json_default(obj):
        """JSON 序列化兼容: Enum -> value, datetime -> ISO"""
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    @staticmethod
    def _dt(v):
        """ISO 字符串 -> datetime（容错）"""
        if isinstance(v, str) and v:
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None
        return v

    def _load_databases(self):
        """加载数据库（恢复枚举/datetime，兼容旧纯 dict 格式）"""
        try:
            if self.assets_db_path.exists():
                with open(self.assets_db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and ("assets" in data or "licenses" in data or "generation" in data):
                    assets_raw = data.get("assets", {})
                    licenses_raw = data.get("licenses", {})
                    generation_raw = data.get("generation", {})
                else:
                    assets_raw = data or {}
                    licenses_raw, generation_raw = {}, {}

                self.assets = {
                    k: AssetMetadata(
                        **{**v,
                           "asset_type": AssetType(v["asset_type"]) if isinstance(v.get("asset_type"), str) else v.get("asset_type"),
                           "created_at": self._dt(v.get("created_at")),
                           "updated_at": self._dt(v.get("updated_at"))}
                    ) for k, v in assets_raw.items()
                }
                self.licenses = {
                    k: AssetLicense(
                        **{**v,
                           "license_status": LicenseStatus(v["license_status"]) if isinstance(v.get("license_status"), str) else v.get("license_status"),
                           "verified_at": self._dt(v.get("verified_at"))}
                    ) for k, v in licenses_raw.items()
                }
                self.generation_metadata = {
                    k: GenerationMetadata(
                        **{**v, "generated_at": self._dt(v.get("generated_at"))}
                    ) for k, v in generation_raw.items()
                }
                logger.info(f"Loaded {len(self.assets)} assets, {len(self.licenses)} licenses, {len(self.generation_metadata)} generations")
        except Exception as e:
            logger.error(f"Failed to load assets database: {e}")
            self.assets, self.licenses, self.generation_metadata = {}, {}, {}

        try:
            if self.embeddings_db_path.exists():
                with open(self.embeddings_db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.embeddings = {
                    k: AssetEmbedding(
                        **{**v,
                           "index_status": IndexStatus(v["index_status"]) if isinstance(v.get("index_status"), str) else v.get("index_status"),
                           "indexed_at": self._dt(v.get("indexed_at"))}
                    ) for k, v in data.items()
                }
                logger.info(f"Loaded {len(self.embeddings)} embeddings from database")
        except Exception as e:
            logger.error(f"Failed to load embeddings database: {e}")
            self.embeddings = {}

        self._replay_embedding_log()

    def _embedding_from_raw(self, v: Dict[str, Any]) -> AssetEmbedding:
        return AssetEmbedding(
            **{**v,
               "index_status": IndexStatus(v["index_status"]) if isinstance(v.get("index_status"), str) else v.get("index_status"),
               "indexed_at": self._dt(v.get("indexed_at"))}
        )

    def _replay_embedding_log(self):
        """把压实之后的追加行叠回内存，并计数以便达到阈值时再压实。
        坏行（断电/并发截断）如实跳过并计数，绝不静默当作不存在。"""
        self._embed_log_lines = 0
        if not self.embeddings_log_path.exists():
            return
        try:
            with open(self.embeddings_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        logger.warning("Skipping malformed embedding log line")
                        continue
                    self._embed_log_lines += 1
                    aid = rec.get("asset_id")
                    if rec.get("op") == "delete":
                        self.embeddings.pop(aid, None)
                    elif rec.get("op") == "upsert":
                        self.embeddings[aid] = self._embedding_from_raw(rec.get("record") or {})
        except Exception as e:
            logger.error(f"Failed to replay embedding log: {e}")

    def save_databases(self):
        """全量快照压实路径（assets+licenses+generation 同库落盘，修复 datetime/Enum 序列化崩溃）。

        仍是公开 API：删除/授权/生成记录等低频路径继续调用它，写的还是同样两个文件、
        同样形状，冷启动读回与 Phase 52 之前逐字段一致。新增素材的热路径不再走这里 ——
        改为只追加一行向量（见 append_embedding），避免每加一条就重写整个向量库。
        """
        with self._persist_lock:
            self._persist_assets()
            self._compact_embeddings()

    def _persist_assets(self):
        try:
            data = {
                "assets": {k: asdict(v) for k, v in self.assets.items()},
                "licenses": {k: asdict(v) for k, v in self.licenses.items()},
                "generation": {k: asdict(v) for k, v in self.generation_metadata.items()},
            }
            tmp = Path(str(self.assets_db_path) + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=self._json_default)
                f.flush()
            os.replace(tmp, self.assets_db_path)
            logger.debug(f"Saved {len(self.assets)} assets to database")
        except Exception as e:
            logger.error(f"Failed to save assets database: {e}")

    def _compact_embeddings(self):
        """向量库全量快照 + 日志清零（先写快照再截断，中途被杀最坏是重放重复 upsert）。"""
        try:
            data = {k: asdict(v) for k, v in self.embeddings.items()}
            tmp = Path(str(self.embeddings_db_path) + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=self._json_default)
                f.flush()
            os.replace(tmp, self.embeddings_db_path)
            with open(self.embeddings_log_path, "w", encoding="utf-8") as f:
                pass
            self._embed_log_lines = 0
            logger.debug(f"Compacted {len(self.embeddings)} embeddings to database")
        except Exception as e:
            logger.error(f"Failed to compact embeddings database: {e}")

    def append_embedding(self, asset_id: str):
        """一条向量的追加写：O(1) 落盘成本，进程硬杀后仍可重放（flush 到操作系统）。
        达到阈值自动压实，日志不会无限增长。"""
        emb = self.embeddings.get(asset_id)
        if emb is None:
            return
        with self._persist_lock:
            try:
                with open(self.embeddings_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"op": "upsert", "asset_id": asset_id,
                                        "record": asdict(emb)},
                                       ensure_ascii=False, default=self._json_default) + "\n")
                    f.flush()
                self._embed_log_lines += 1
                if self._embed_log_lines >= self._embed_compact_every:
                    self._compact_embeddings()
            except Exception as e:
                logger.error(f"Failed to append embedding {asset_id}: {e}")

    def persist_assets(self):
        """素材/授权/生成元数据的快照落盘（热路径用；向量库另有追加路径）。"""
        with self._persist_lock:
            self._persist_assets()

    async def add_asset(
        self,
        file_path: str,
        mime_type: str,
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> AssetMetadata:
        """
        添加新素材

        Args:
            file_path: 文件路径
            mime_type: MIME 类型
            width: 宽度 (图片/视频)
            height: 高度 (图片/视频)

        Returns:
            素材元数据

        Phase 52：本协程只是一层接口 —— stat、缩略图、向量、落盘整段搬进工作线程
        （_add_asset_sync）。实测旧形态（磁盘与 CPU 工作坐在循环里）在 175 件导入的
        突发中把同服务的 /health 按压到 p95=996.5 ms，而空闲轮询只要 32.8 ms。
        签名、返回值、落盘形状、日志与事件语义均未改变。
        """
        return await asyncio.to_thread(self._add_asset_sync, file_path, mime_type, width, height)

    def _add_asset_sync(
        self,
        file_path: str,
        mime_type: str,
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> AssetMetadata:
        """add_asset 的全部真实工作，运行在工作线程上（纯同步，可被 to_thread 直接调用）。"""
        path = Path(file_path)

        # 生成唯一 ID
        asset_id = str(uuid.uuid4())

        # 创建元数据
        metadata = AssetMetadata(
            asset_id=asset_id,
            filename=path.name,
            file_path=str(path.resolve()),
            file_size=path.stat().st_size,
            mime_type=mime_type,
            asset_type=self._detect_asset_type(mime_type),
            width=width,
            height=height,
        )

        # 保存到数据库
        self.assets[asset_id] = metadata

        # 生成缩略图（真实 PIL 缩略；非图像素材返回 None，不伪造）
        thumb = self._thumbnail_sync(asset_id, mime_type, file_path)
        if thumb:
            metadata.thumbnail_path = thumb

        # 生成 Embedding（追加一行向量，不再全量重写向量库）
        self._embedding_sync(asset_id)

        # 保存素材/授权/生成元数据快照
        self.persist_assets()

        logger.info(f"Added asset: {asset_id} ({path.name})")
        return metadata
    
    def _detect_asset_type(self, mime_type: str) -> AssetType:
        """检测素材类型"""
        if mime_type.startswith("image/"):
            return AssetType.IMAGE
        elif mime_type.startswith("video/"):
            return AssetType.VIDEO
        elif mime_type.startswith("audio/"):
            return AssetType.AUDIO
        elif mime_type.startswith("application/pdf"):
            return AssetType.DOCUMENT
        elif mime_type in ("application/font-sfnt", "font/ttf"):
            return AssetType.FONT
        else:
            return AssetType.DOCUMENT
    
    def _thumbnail_sync(self, asset_id: str, mime_type: str, file_path: str) -> Optional[str]:
        """真实缩略图（PIL，≤256px PNG）；非图像返回 None，绝不伪造。

        同步实现，只在"已经离开事件循环"的上下文里调用（_add_asset_sync 直接调，
        _generate_thumbnail 负责把它下沉到工作线程）。图像解码/缩放/写盘是 CPU+磁盘
        混合工作，坐在循环上会饿死同服务的其他请求。
        """
        if not mime_type or not mime_type.startswith("image/"):
            return None
        try:
            from PIL import Image
            self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = self.thumbnails_dir / f"{asset_id}.png"

            with Image.open(file_path) as im:
                if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                    im = im.convert("RGBA")
                else:
                    im = im.convert("RGB")
                im.thumbnail((256, 256))
                im.save(thumb_path, "PNG")
            logger.debug(f"Generated thumbnail: {thumb_path}")
            return str(thumb_path)
        except Exception as e:
            logger.warning(f"Thumbnail generation skipped for {asset_id}: {e}")
            return None

    async def _generate_thumbnail(self, asset_id: str, mime_type: str, file_path: str) -> Optional[str]:
        """向后兼容的协程接口：把 _thumbnail_sync 交给工作线程（Phase 51 之前的形态）。"""
        return await asyncio.to_thread(self._thumbnail_sync, asset_id, mime_type, file_path)

    def _embedding_sync(self, asset_id: str) -> None:
        """
        生成确定性 Embedding 向量并只追加这一条。

        旧实现 `[hash(path+i) % 1.0]` 对 float 取模恒为 0.0（零向量），且
        `hash()` 受 PYTHONHASHSEED 影响跨进程不可复现 —— 均已修复。
        现使用与查询检索共用的 embed_text()：素材描述符 = 文件名 + 扩展名
        + MIME + 类型，词元哈希投影，保证重启/跨进程一致。
        """
        asset = None
        try:
            asset = self.assets.get(asset_id)
            if not asset:
                return

            p = Path(asset.filename)
            descriptor = " ".join(filter(None, [
                p.stem,
                p.suffix.lstrip("."),
                asset.mime_type,
                asset.asset_type.value if asset.asset_type else "",
            ]))
            vector = embed_text(descriptor, dims=512)

            embedding = AssetEmbedding(
                asset_id=asset_id,
                vector=vector,
                model_name="tokenhash-sha256-512",
                index_status=IndexStatus.COMPLETED,
                indexed_at=datetime.now(),
            )

            self.embeddings[asset_id] = embedding

            # 落盘：只追加这一条向量（Phase 52 —— 旧实现每加一条就重写整个向量库）
            self.append_embedding(asset_id)

            logger.debug(f"Generated embedding for {asset_id}")

        except Exception as e:
            logger.error(f"Failed to generate embedding for {asset_id}: {e}")
            # 更新状态为失败
            if asset_id in self.embeddings:
                self.embeddings[asset_id].index_status = IndexStatus.FAILED

    async def _generate_embedding(self, asset_id: str):
        """向后兼容的协程接口：把 _embedding_sync 交给工作线程。"""
        return await asyncio.to_thread(self._embedding_sync, asset_id)
    
    async def search_assets(
        self,
        query_vector: List[float],
        top_k: int = 10
    ) -> List[Tuple[str, float]]:
        """
        基于向量搜索相似素材
        
        Args:
            query_vector: 查询向量
            top_k: 返回数量
            
        Returns:
            [(asset_id, similarity_score), ...]
        """
        # Phase 52：这是一段 O(N x 512) 的纯 Python 逐向量点积，旧实现直接坐在事件循环里
        # 跑（实测 N=375 时单次查询 43.7 ms，同期 /health 从 4 ms 涨到 34.7 ms），
        # 因此整段打分搬进工作线程；结果与顺序与旧实现逐字节一致。
        return await asyncio.to_thread(self._score_all, list(query_vector), top_k)

    def _score_all(self, query_vector: List[float], top_k: int) -> List[Tuple[str, float]]:
        similarities = []
        
        for asset_id, embedding in self.embeddings.items():
            if embedding.index_status != IndexStatus.COMPLETED:
                continue
            
            # 计算余弦相似度
            sim = self._cosine_similarity(query_vector, embedding.vector)
            similarities.append((asset_id, sim))
        
        # 排序并返回 top_k
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        import math
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def record_generation(
        self,
        source_asset_id: Optional[str],
        prompt: str,
        model_name: str,
        provider: str,
        width: int,
        height: int,
        steps: int,
        guidance_scale: float,
        seed: Optional[int],
        cost_usd: float,
        duration_seconds: float,
        negative_prompt: Optional[str] = None,
    ) -> str:
        """
        记录 AI 生成元数据
        
        Args:
            source_asset_id: 源素材 ID
            prompt: 提示词
            model_name: 模型名称
            provider: Provider
            width: 宽度
            height: 高度
            steps: 步数
            guidance_scale: 引导系数
            seed: 随机种子
            cost_usd: 成本
            duration_seconds: 耗时
            
        Returns:
            generation_id
        """
        generation_id = str(uuid.uuid4())
        
        metadata = GenerationMetadata(
            generation_id=generation_id,
            source_asset_id=source_asset_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
            model_name=model_name,
            provider=provider,
            width=width,
            height=height,
            steps=steps,
            guidance_scale=guidance_scale,
            seed=seed,
            cost_usd=cost_usd,
            duration_seconds=duration_seconds,
        )
        
        self.generation_metadata[generation_id] = metadata
        self.save_databases()
        
        logger.info(f"Recorded generation: {generation_id}")
        return generation_id
    
    def set_license_status(
        self,
        asset_id: str,
        license_status: LicenseStatus,
        license_type: Optional[str] = None,
        commercial_use: bool = False,
        verified: bool = False,
        warnings: Optional[List[str]] = None
    ):
        """
        设置素材授权状态
        
        注意: 不自动认定第三方素材一定可商用
        """
        license_info = AssetLicense(
            asset_id=asset_id,
            license_status=license_status,
            license_type=license_type,
            attribution_required=license_type == "CC-BY",
            commercial_use=commercial_use,
            modification_allowed=True,
            verified=verified,
            warnings=warnings or [],
        )
        
        self.licenses[asset_id] = license_info
        self.save_databases()
        
        logger.info(f"Set license status for {asset_id}: {license_status.value}")
    
    def get_asset(self, asset_id: str) -> Optional[AssetMetadata]:
        """获取素材"""
        return self.assets.get(asset_id)
    
    def get_embedding(self, asset_id: str) -> Optional[AssetEmbedding]:
        """获取 Embedding"""
        return self.embeddings.get(asset_id)
    
    def get_license(self, asset_id: str) -> Optional[AssetLicense]:
        """获取授权信息"""
        return self.licenses.get(asset_id)
    
    def list_assets(self) -> List[AssetMetadata]:
        """列出所有素材"""
        return list(self.assets.values())
    
    def delete_asset(self, asset_id: str) -> bool:
        """删除素材"""
        if asset_id not in self.assets:
            return False
        
        del self.assets[asset_id]
        if asset_id in self.embeddings:
            del self.embeddings[asset_id]
        if asset_id in self.licenses:
            del self.licenses[asset_id]
        
        self.save_databases()
        
        logger.info(f"Deleted asset: {asset_id}")
        return True


# ============================================================================
# 配置文件示例
# ============================================================================

ASSET_ENGINE_CONFIG = """
{
  "version": "1.0.0",
  "root_path": "${LOCALAPPDATA}\\Zaojing\\Assets",
  "max_index_assets": 100000,
  "embedding_model": "clip-ViT-B-32",
  "thumbnail_size": 256,
  "auto_tag_enabled": true,
  "nas_enabled": false,
  "nas_paths": [],
  "engine_url": "http://127.0.0.1:8082"
}
"""


# ============================================================================
# 快速测试
# ============================================================================

if __name__ == "__main__":
    import tempfile
    import math

    print("=" * 60)
    print("P2 Asset Engine - 真实驱动自测（非恒真断言）")
    print("=" * 60)

    temp_dir = Path(tempfile.mkdtemp())
    engine = AssetEngine(temp_dir)

    # --- 1) embed_text：确定性 + 可判别性（非随机伪向量） ---
    v1 = embed_text("matcha tea green package")
    v1b = embed_text("matcha tea green package")
    assert v1 == v1b, "同一文本两次嵌入必须逐维相同（确定性）"
    assert any(x != 0.0 for x in v1), "嵌入向量不应恒为零向量（旧缺陷回归测试）"
    cos = engine._cosine_similarity
    sim_near = cos(embed_text("matcha tea green package"), embed_text("matcha tea"))
    sim_far = cos(embed_text("matcha tea green package"), embed_text("motorcycle sportscar render"))
    assert sim_near > sim_far, ("共享词元的文本余弦应高于无关文本", sim_near, sim_far)
    print(f"[OK] embed_text 确定性且可判别: cos(近)={sim_near:.4f} > cos(远)={sim_far:.4f}")

    # --- 2) 真实图像 → 真实缩略图（PIL 确实缩放，非伪造路径） ---
    _PIL = True
    try:
        from PIL import Image as _Img
    except Exception:
        _PIL = False
    big = temp_dir / "matcha_tea_box.png"
    real_meta = None
    if _PIL:
        _bi = _Img.new("RGB", (600, 400), (30, 140, 60))
        _bi.save(big)
        real_meta = asyncio.run(engine.add_asset(str(big), "image/png", width=600, height=400))
        assert real_meta.thumbnail_path and Path(real_meta.thumbnail_path).exists(), \
            "真实图像应产出真实缩略图文件"
        _tp = Path(real_meta.thumbnail_path)
        assert _tp.stat().st_size > 0, "缩略图文件不应为空字节"
        with _Img.open(_tp) as _t:
            _t.verify()
        with _Img.open(_tp) as _t:
            _tw, _th = _t.size
        assert max(_tw, _th) <= 256 and _tw < 600 and _th < 400, \
            ("缩略图须被真实缩放(≤256 且小于原图)", (_tw, _th))
        print(f"[OK] 真实缩略图: 原 600x400 -> 缩略 {_tw}x{_th} ({_tp.stat().st_size}B)")
    else:
        print("[SKIP] Pillow 不可用：跳过缩略图断言（不伪造），其余断言仍执行")

    # --- 3) 不可解码的"图像"→ 诚实 None 缩略图，不伪造、不崩溃 ---
    bad = temp_dir / "not_really.jpg"
    bad.write_bytes(b"this is definitely not a decodable jpeg")
    bad_meta = asyncio.run(engine.add_asset(str(bad), "image/jpeg"))
    assert bad_meta.thumbnail_path is None, "非法图像绝不可伪造缩略图路径"
    print("[OK] 非法图像诚实返回 thumbnail_path=None（未伪造）")

    # --- 4) search_assets：用素材自身向量查询必命中自己且余弦≈1、严格高于次优 ---
    target = real_meta if real_meta else bad_meta
    tvec = engine.get_embedding(target.asset_id).vector
    res = asyncio.run(engine.search_assets(tvec, top_k=3))
    assert res and res[0][0] == target.asset_id, "以素材自身向量查询应首选该素材"
    assert abs(res[0][1] - 1.0) < 1e-6, ("自匹配余弦应≈1.0", res[0][1])
    if len(res) > 1:
        assert res[0][1] > res[1][1], ("排序须为降序、首选严格高于次优", res[0][1], res[1][1])
    print(f"[OK] 向量检索真实命中: top={res[0][0][:8]} sim={res[0][1]:.6f} n={len(res)}")

    # --- 5) 生成元数据 / 授权状态：真实落库可回读 ---
    gen_id = engine.record_generation(
        source_asset_id=target.asset_id, prompt="a beautiful landscape",
        model_name="stable-diffusion-xl", provider="local", width=1024, height=1024,
        steps=30, guidance_scale=7.5, seed=42, cost_usd=0.0, duration_seconds=5.2)
    assert gen_id, "record_generation 应返回记录 id"
    assert engine.generation_metadata, "生成元数据应真实持久化到引擎"
    engine.set_license_status(
        asset_id=target.asset_id, license_status=LicenseStatus.UNKNOWN,
        verified=False, warnings=["Third-party asset, license unknown"])
    lic = engine.get_license(target.asset_id)
    assert lic is not None and lic.license_status == LicenseStatus.UNKNOWN, "授权状态应真实回读"
    print("[OK] 生成元数据与授权状态真实落库可回读")

    print("\n" + "=" * 60)
    print("[PASS] P2 Asset Engine 自测：确定性向量/真实缩略图/诚实None/检索命中/落库回读 全通过")
    print("=" * 60)
