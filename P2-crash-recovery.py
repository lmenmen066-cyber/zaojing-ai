"""
造境 AI 设计智能体 - Crash Recovery Engine 完整实现

包含:
1. Snapshot 机制 (文档快照)
2. Transaction 事务管理
3. Rollback 回滚
4. Checkpoint 检查点
5. Operation Journal 操作日志
6. Emergency Recovery 紧急恢复

版本：V1.0
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, asdict, field
from enum import Enum
from datetime import datetime
import shutil
import hashlib
import asyncio

logger = logging.getLogger(__name__)


def _json_default(obj: Any) -> Any:
    """json.dumps 的 default：把 datetime 序列化为 ISO 字符串（其余不可序列化对象降级为 str）。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def _restore_datetimes(data: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    """把已加载字典中的指定字段从 ISO 字符串还原为 datetime（尽力而为）。"""
    for field_name in fields:
        val = data.get(field_name)
        if isinstance(val, str):
            try:
                data[field_name] = datetime.fromisoformat(val)
            except ValueError:
                pass
    return data


# ============================================================================
# 枚举定义
# ============================================================================

class RecoveryLevel(Enum):
    """恢复级别"""
    UNDO = "undo"              # 撤销上一步
    TASK_ROLLBACK = "task_rollback"  # 回滚到任务开始
    VERSION_ROLLBACK = "version_rollback"  # 回滚到版本
    DOCUMENT_RECOVERY = "document_recovery"  # 文档恢复
    PROJECT_RECOVERY = "project_recovery"    # 项目恢复
    EMERGENCY_RECOVERY = "emergency_recovery"  # 紧急恢复


class OperationType(Enum):
    """操作类型"""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"
    RENAME = "rename"
    APPLY_FILTER = "apply_filter"
    GENERATE_AI = "generate_ai"
    EXPORT = "export"


@dataclass
class Operation:
    """操作记录"""
    id: str
    type: OperationType
    timestamp: datetime
    target_path: str
    target_name: str
    before_state: Optional[Dict[str, Any]] = None  # 操作前状态
    after_state: Optional[Dict[str, Any]] = None   # 操作后状态
    
    # 元数据
    user_id: Optional[str] = None
    task_id: Optional[str] = None
    description: Optional[str] = None


@dataclass
class Snapshot:
    """快照"""
    id: str
    created_at: datetime
    snapshot_type: str  # full / incremental
    
    # 内容
    document_path: str
    checksum: str
    size_bytes: int

    # full 快照时保存的字节级备份副本路径（None = 仅元数据快照，无本地可恢复副本）
    backup_path: Optional[str] = None

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # 操作列表 (增量快照)
    operations: List[Operation] = field(default_factory=list)


@dataclass
class TaskState:
    """任务状态"""
    task_id: str
    status: str  # running / paused / completed / failed / interrupted
    
    # 执行进度
    current_step: int
    total_steps: int
    
    # 上下文
    context: Dict[str, Any] = field(default_factory=dict)
    
    # 时间戳
    started_at: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # 错误信息
    error_message: Optional[str] = None
    
    # 恢复点
    checkpoint_snapshot_id: Optional[str] = None


# ============================================================================
# Snapshot Manager - 快照管理器
# ============================================================================

class SnapshotManager:
    """快照管理器"""
    
    def __init__(self, snapshot_dir: Path):
        """
        初始化
        
        Args:
            snapshot_dir: 快照存储目录
        """
        self.snapshot_dir = snapshot_dir
        self.snapshots: Dict[str, Snapshot] = {}
        
        # 确保目录存在
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载现有快照
        self._load_snapshots()
    
    def _load_snapshots(self):
        """加载现有快照"""
        snapshot_files = list(self.snapshot_dir.glob("*.json"))
        
        for file_path in snapshot_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _restore_datetimes(data, ["created_at"])
                    snapshot = Snapshot(**data)
                    self.snapshots[snapshot.id] = snapshot
                logger.debug(f"Loaded snapshot: {snapshot.id}")
            except Exception as e:
                logger.error(f"Failed to load snapshot {file_path}: {e}")
    
    def _stage_backup(self, snapshot_id: str, doc: Path):
        """一份快照的全部磁盘工作（读校验 + 字节副本），收进一个同步块，
        由调用方搬到工作线程执行。Phase 52 实测：120 MB 的 checksum+copyfile
        直接坐在事件循环里会把 /health 轮询拖到 661 ms（空闲基线 4 ms）。
        返回 (checksum, file_size, backup_path)；副本写失败绝不谎报可恢复。"""
        checksum = self._calculate_checksum(str(doc))
        file_size = doc.stat().st_size
        backup_file = self.snapshot_dir / f"{snapshot_id}.bak"
        try:
            shutil.copyfile(str(doc), str(backup_file))
            return checksum, file_size, str(backup_file)
        except Exception as e:  # 副本写入失败 —— 不谎报，标记不可恢复
            logger.error(f"Failed to write snapshot backup for {doc}: {e}")
            return "error", file_size, None

    async def create_snapshot(
        self,
        document_path: str,
        snapshot_type: str = "incremental",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Snapshot:
        """
        创建快照
        
        Args:
            document_path: 文档路径
            snapshot_type: full / incremental
            metadata: 元数据
            
        Returns:
            创建的快照
        """
        snapshot_id = str(uuid.uuid4())

        doc = Path(document_path) if document_path else None
        has_local_file = bool(doc and doc.is_file())

        backup_path: Optional[str] = None
        if has_local_file:
            # full / incremental 快照都保存字节级副本，供真实恢复使用。
            # Phase 52：整段磁盘工作搬到工作线程，事件循环不再被 120 MB 按住。
            checksum, file_size, backup_path = await asyncio.to_thread(
                self._stage_backup, snapshot_id, doc)
        else:
            # Adobe 文档尚未导出为本地路径 / 空路径 —— 诚实记录为不可本地恢复
            checksum, file_size = "none", 0

        metadata = dict(metadata or {})
        if not has_local_file:
            metadata["recoverable"] = False
            metadata["recovery_reason"] = "no_local_file (Adobe 文档需宿主导出后可快照)"
        elif backup_path is None:
            metadata["recoverable"] = False
            metadata["recovery_reason"] = "backup_write_failed"
        else:
            metadata["recoverable"] = True

        snapshot = Snapshot(
            id=snapshot_id,
            created_at=datetime.now(),
            snapshot_type=snapshot_type,
            document_path=document_path,
            checksum=checksum,
            size_bytes=file_size,
            backup_path=backup_path,
            metadata=metadata,
        )

        # 保存快照
        await self._save_snapshot(snapshot)

        # 添加到内存
        self.snapshots[snapshot_id] = snapshot

        logger.info(
            f"Created {snapshot_type} snapshot: {snapshot_id} "
            f"(recoverable={metadata.get('recoverable')})"
        )

        return snapshot
    
    async def _save_snapshot(self, snapshot: Snapshot):
        """保存快照到磁盘（磁盘写在 Phase 52 移入工作线程，循环不再等待落盘）"""
        file_path = self.snapshot_dir / f"{snapshot.id}.json"
        await asyncio.to_thread(self._write_snapshot_file, file_path, asdict(snapshot))

    def _write_snapshot_file(self, file_path: Path, data: Dict[str, Any]):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=_json_default)
    
    def _calculate_checksum(self, file_path: str) -> str:
        """计算文件 checksum"""
        hasher = hashlib.sha256()
        
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hasher.update(chunk)
            
            return hasher.hexdigest()
        except Exception as e:
            logger.error(f"Failed to calculate checksum: {e}")
            return "error"
    
    def get_snapshot(self, snapshot_id: str) -> Optional[Snapshot]:
        """获取快照"""
        return self.snapshots.get(snapshot_id)
    
    def list_snapshots(self, document_path: Optional[str] = None) -> List[Snapshot]:
        """列出快照"""
        if document_path:
            return [
                s for s in self.snapshots.values()
                if s.document_path == document_path
            ]
        return list(self.snapshots.values())
    
    def _restore_files(self, backup: Path, document_path: str) -> str:
        """一次恢复的全部磁盘工作（副本写回 + 恢复后重算哈希），收进同步块供工作线程执行。
        返回恢复后的 checksum；读失败时 _calculate_checksum 如实返回 "error"，
        由调用方的完整性比较判为恢复失败，绝不谎报成功。"""
        shutil.copyfile(str(backup), document_path)
        return self._calculate_checksum(document_path)

    async def restore_snapshot(self, snapshot_id: str) -> bool:
        """
        恢复快照
        
        Args:
            snapshot_id: 快照 ID
            
        Returns:
            是否成功
        """
        snapshot = self.snapshots.get(snapshot_id)
        if not snapshot:
            logger.error(f"Snapshot not found: {snapshot_id}")
            return False

        if not snapshot.backup_path:
            logger.warning(
                f"Snapshot {snapshot_id} has no local backup "
                f"({snapshot.metadata.get('recovery_reason', 'metadata_only')}) — 无法本地恢复"
            )
            return False

        backup = Path(snapshot.backup_path)
        if not backup.is_file():
            logger.error(f"Backup file missing for snapshot {snapshot_id}: {backup}")
            return False

        logger.info(f"Restoring snapshot: {snapshot_id} -> {snapshot.document_path}")

        try:
            # Phase 52：副本写回 + 全文件重算哈希一并移入工作线程（120 MB 恢复实测
            # 曾把 /health 按住 581 ms），循环不再等待磁盘。
            restored_checksum = await asyncio.to_thread(
                self._restore_files, backup, snapshot.document_path)
        except Exception as e:
            logger.error(f"Restore failed for {snapshot_id}: {e}")
            return False

        # 完整性校验：恢复后重新计算 checksum 必须与快照记录一致
        if restored_checksum != snapshot.checksum:
            logger.error(
                f"Integrity mismatch after restore {snapshot_id}: "
                f"expected {snapshot.checksum}, got {restored_checksum}"
            )
            return False

        logger.info(f"Snapshot restored & verified: {snapshot_id}")
        return True


# ============================================================================
# Transaction Manager - 事务管理器
# ============================================================================

class TransactionManager:
    """事务管理器"""
    
    def __init__(self, snapshot_manager: SnapshotManager):
        """
        初始化
        
        Args:
            snapshot_manager: 快照管理器
        """
        self.snapshot_manager = snapshot_manager
        self.active_transactions: Dict[str, dict] = {}
        self.operation_journal: List[Operation] = []
    
    async def begin_transaction(
        self,
        task_id: str,
        user_id: Optional[str] = None,
        document_path: str = ""
    ) -> str:
        """
        开始事务
        
        Args:
            task_id: 任务 ID
            user_id: 用户 ID
            document_path: 当前工作文档本地路径（可选；为空则创建仅元数据快照）
            
        Returns:
            事务 ID
        """
        transaction_id = str(uuid.uuid4())
        
        # 创建初始快照
        initial_snapshot = await self.snapshot_manager.create_snapshot(
            document_path=document_path,
            snapshot_type="transaction_start",
            metadata={"task_id": task_id, "user_id": user_id}
        )
        
        self.active_transactions[transaction_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "snapshot_id": initial_snapshot.id,
            "operations": [],
            "started_at": datetime.now(),
        }
        
        logger.info(f"Started transaction: {transaction_id}")
        
        return transaction_id
    
    async def commit_transaction(self, transaction_id: str) -> bool:
        """
        提交事务
        
        Args:
            transaction_id: 事务 ID
            
        Returns:
            是否成功
        """
        transaction = self.active_transactions.get(transaction_id)
        if not transaction:
            logger.error(f"Transaction not found: {transaction_id}")
            return False
        
        logger.info(f"Committing transaction: {transaction_id}")
        
        # 创建最终快照
        final_snapshot = await self.snapshot_manager.create_snapshot(
            document_path="",
            snapshot_type="transaction_commit",
            metadata={
                "task_id": transaction["task_id"],
                "operation_count": len(transaction["operations"]),
            }
        )
        
        # 清除活动事务
        del self.active_transactions[transaction_id]
        
        return True
    
    async def rollback_transaction(self, transaction_id: str) -> bool:
        """
        回滚事务
        
        Args:
            transaction_id: 事务 ID
            
        Returns:
            是否成功
        """
        transaction = self.active_transactions.get(transaction_id)
        if not transaction:
            logger.error(f"Transaction not found: {transaction_id}")
            return False
        
        logger.info(f"Rolling back transaction: {transaction_id}")
        
        # 恢复到初始快照
        success = await self.snapshot_manager.restore_snapshot(
            transaction["snapshot_id"]
        )
        
        # 清除活动事务
        del self.active_transactions[transaction_id]
        
        return success
    
    def record_operation(self, transaction_id: str, operation: Operation):
        """记录操作"""
        transaction = self.active_transactions.get(transaction_id)
        if transaction:
            transaction["operations"].append(operation)
            self.operation_journal.append(operation)
    
    def get_transaction_history(self, task_id: str) -> List[Dict]:
        """获取事务历史"""
        history = []
        
        for tid, transaction in self.active_transactions.items():
            if transaction["task_id"] == task_id:
                history.append({
                    "transaction_id": tid,
                    "status": "active",
                    "started_at": transaction["started_at"].isoformat(),
                    "operation_count": len(transaction["operations"]),
                })
        
        return history


# ============================================================================
# Crash Recovery Engine - 崩溃恢复引擎
# ============================================================================

class CrashRecoveryEngine:
    """崩溃恢复引擎"""
    
    def __init__(
        self,
        snapshot_dir: Path,
        state_dir: Path
    ):
        """
        初始化
        
        Args:
            snapshot_dir: 快照目录
            state_dir: 状态存储目录
        """
        self.snapshot_manager = SnapshotManager(snapshot_dir)
        self.transaction_manager = TransactionManager(self.snapshot_manager)
        
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        self.task_states: Dict[str, TaskState] = {}
        
        # 加载状态
        self._load_task_states()
    
    def _load_task_states(self):
        """加载任务状态"""
        state_files = list(self.state_dir.glob("task_*.json"))
        
        for file_path in state_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _restore_datetimes(data, ["started_at", "last_heartbeat", "completed_at"])
                    state = TaskState(**data)
                    self.task_states[state.task_id] = state
                
                logger.debug(f"Loaded task state: {state.task_id}")
            except Exception as e:
                logger.error(f"Failed to load task state {file_path}: {e}")
    
    def save_task_state(self, state: TaskState):
        """保存任务状态"""
        file_path = self.state_dir / f"task_{state.task_id}.json"
        
        data = asdict(state)
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=_json_default)
    
    async def register_task(
        self,
        task_id: str,
        total_steps: int,
        context: Optional[Dict[str, Any]] = None
    ) -> TaskState:
        """
        注册任务
        
        Args:
            task_id: 任务 ID
            total_steps: 总步骤数
            context: 执行上下文
            
        Returns:
            任务状态
        """
        state = TaskState(
            task_id=task_id,
            status="running",
            current_step=0,
            total_steps=total_steps,
            context=context or {},
            started_at=datetime.now(),
            last_heartbeat=datetime.now(),
        )
        
        self.task_states[task_id] = state
        await asyncio.to_thread(self.save_task_state, state)
        
        logger.info(f"Registered task: {task_id} ({total_steps} steps)")
        
        return state
    
    async def update_task_progress(
        self,
        task_id: str,
        current_step: int,
        heartbeat: bool = True
    ):
        """
        更新任务进度
        
        Args:
            task_id: 任务 ID
            current_step: 当前步骤
            heartbeat: 是否发送心跳
        """
        state = self.task_states.get(task_id)
        if not state:
            raise ValueError(f"Task not found: {task_id}")
        
        state.current_step = current_step
        
        if heartbeat:
            state.last_heartbeat = datetime.now()
        
        await asyncio.to_thread(self.save_task_state, state)
    
    async def complete_task(self, task_id: str):
        """完成任务"""
        state = self.task_states.get(task_id)
        if not state:
            raise ValueError(f"Task not found: {task_id}")
        
        state.status = "completed"
        state.completed_at = datetime.now()
        
        await asyncio.to_thread(self.save_task_state, state)
        
        logger.info(f"Completed task: {task_id}")
    
    async def fail_task(
        self,
        task_id: str,
        error_message: str,
        recoverable: bool = True
    ):
        """
        任务失败
        
        Args:
            task_id: 任务 ID
            error_message: 错误信息
            recoverable: 是否可恢复
        """
        state = self.task_states.get(task_id)
        if not state:
            raise ValueError(f"Task not found: {task_id}")
        
        state.status = "interrupted" if recoverable else "failed"
        state.error_message = error_message
        state.last_heartbeat = datetime.now()
        
        await asyncio.to_thread(self.save_task_state, state)
        
        logger.error(f"Task failed: {task_id} - {error_message}")
    
    async def check_orphaned_tasks(self) -> List[TaskState]:
        """
        检查悬空任务 (无心跳超过阈值)
        
        Returns:
            悬空任务列表
        """
        orphaned = []
        threshold_minutes = 10  # 10 分钟无心跳
        
        for task_id, state in self.task_states.items():
            if state.status == "running" and state.last_heartbeat:
                elapsed = (datetime.now() - state.last_heartbeat).total_seconds()
                
                if elapsed > threshold_minutes * 60:
                    orphaned.append(state)
                    logger.warning(f"Orphaned task detected: {task_id} ({elapsed}s)")
        
        return orphaned
    
    async def recover_task(
        self,
        task_id: str,
        recovery_level: RecoveryLevel = RecoveryLevel.TASK_ROLLBACK
    ) -> Optional[Snapshot]:
        """
        恢复任务
        
        Args:
            task_id: 任务 ID
            recovery_level: 恢复级别
            
        Returns:
            恢复的快照
        """
        state = self.task_states.get(task_id)
        if not state:
            raise ValueError(f"Task not found: {task_id}")
        
        logger.info(f"Recovering task: {task_id} (level={recovery_level.value})")
        
        # 根据恢复级别选择快照
        if recovery_level == RecoveryLevel.UNDO:
            # 撤销上一步：回到当前恢复点之前最近一个仍可本地恢复的快照（真实确定性选择）
            snapshot_id = self._select_undo_snapshot(task_id, state)
        else:
            # TASK_ROLLBACK 及默认级别：回到任务检查点快照
            snapshot_id = state.checkpoint_snapshot_id
        
        if snapshot_id:
            success = await self.snapshot_manager.restore_snapshot(snapshot_id)
            
            if success:
                # 重置任务状态
                state.status = "running"
                state.error_message = None
                state.started_at = datetime.now()
                state.last_heartbeat = datetime.now()
                # 将恢复点前移到刚还原的快照：对 UNDO 使后续撤销继续回退一步，
                # 对 TASK_ROLLBACK 因还原的即检查点本身而为无害空操作。
                state.checkpoint_snapshot_id = snapshot_id
                
                await asyncio.to_thread(self.save_task_state, state)
                
                logger.info(f"Task recovered: {task_id}")
                
                return self.snapshot_manager.get_snapshot(snapshot_id)
        
        return None
    
    def _select_undo_snapshot(self, task_id: str, state: "TaskState") -> Optional[str]:
        """为 UNDO（撤销上一步）真实地选出当前恢复点之前最近一个仍可本地恢复的快照。

        纯本地确定性逻辑：按创建时间排序任务相关快照，定位当前恢复点（检查点，
        无检查点时视为最新一个），从其前一个开始回溯，返回第一个仍带本地备份副本
        （backup_path 非空）的快照 ID。若历史不足两步、或当前恢复点之前没有可本地
        恢复的快照，诚实返回 None（表示没有可撤销的上一步），绝不伪造一次恢复。
        """
        history = [
            s for s in self.snapshot_manager.list_snapshots()
            if s.metadata.get("task_id") == task_id
        ]
        # 任务未打过带 task_id 的快照时，退回到与当前检查点同一文档的快照序列。
        if not history and state.checkpoint_snapshot_id:
            cp = self.snapshot_manager.get_snapshot(state.checkpoint_snapshot_id)
            if cp:
                history = [
                    s for s in self.snapshot_manager.list_snapshots()
                    if s.document_path == cp.document_path
                ]
        if len(history) < 2:
            logger.info(
                f"UNDO: 无可撤销的上一步（task={task_id}, 历史快照={len(history)}）"
            )
            return None

        history.sort(key=lambda s: s.created_at)

        # 定位当前恢复点索引：优先检查点，否则视为最新一个。
        if state.checkpoint_snapshot_id:
            idxs = [
                i for i, s in enumerate(history)
                if s.id == state.checkpoint_snapshot_id
            ]
            cur = idxs[0] if idxs else len(history) - 1
        else:
            cur = len(history) - 1

        # 从当前恢复点往前回溯，找第一个仍可本地恢复的快照。
        for j in range(cur - 1, -1, -1):
            if history[j].backup_path:
                return history[j].id

        logger.info(
            f"UNDO: 当前恢复点之前没有可本地恢复的快照（task={task_id}）"
        )
        return None
    
    def generate_recovery_report(self, task_id: str) -> Dict[str, Any]:
        """
        生成恢复报告
        
        Args:
            task_id: 任务 ID
            
        Returns:
            报告数据
        """
        state = self.task_states.get(task_id)
        if not state:
            return {"error": "Task not found"}
        
        # 查找相关快照
        snapshots = self.snapshot_manager.list_snapshots()
        
        return {
            "task_id": task_id,
            "status": state.status,
            "current_step": state.current_step,
            "total_steps": state.total_steps,
            "error_message": state.error_message,
            "last_heartbeat": state.last_heartbeat.isoformat() if state.last_heartbeat else None,
            "checkpoint_snapshot": state.checkpoint_snapshot_id,
            "available_snapshots": [
                {"id": s.id, "created_at": s.created_at.isoformat()}
                for s in snapshots[-10:]  # 最近 10 个
            ],
            "recommendations": self._generate_recommendations(state),
        }
    
    def _generate_recommendations(self, state: TaskState) -> List[str]:
        """生成恢复建议"""
        recommendations = []
        
        if state.error_message:
            recommendations.append(f"错误：{state.error_message}")
        
        if state.checkpoint_snapshot_id:
            recommendations.append(f"可从检查点快照恢复：{state.checkpoint_snapshot_id}")
        
        recommendations.append("建议执行完全恢复流程")
        
        return recommendations


# ============================================================================
# Emergency Recovery - 紧急恢复工具
# ============================================================================

class EmergencyRecovery:
    """紧急恢复工具"""
    
    @staticmethod
    async def recover_from_crash(
        recovery_engine: CrashRecoveryEngine,
        document_path: str
    ) -> Dict[str, Any]:
        """
        从崩溃中恢复
        
        Args:
            recovery_engine: 恢复引擎
            document_path: 文档路径
            
        Returns:
            恢复结果
        """
        results = {
            "success": False,
            "tasks_found": 0,
            "tasks_recovered": 0,
            "snapshots_available": 0,
            "details": [],
        }
        
        # 查找所有悬空任务
        orphaned_tasks = await recovery_engine.check_orphaned_tasks()
        results["tasks_found"] = len(orphaned_tasks)
        
        # 恢复每个任务
        for task in orphaned_tasks:
            snapshot = await recovery_engine.recover_task(
                task.task_id,
                RecoveryLevel.TASK_ROLLBACK
            )
            
            if snapshot:
                results["tasks_recovered"] += 1
                results["details"].append({
                    "task_id": task.task_id,
                    "recovered_to": snapshot.id,
                    "status": "success",
                })
            else:
                results["details"].append({
                    "task_id": task.task_id,
                    "status": "failed",
                    "reason": "No suitable snapshot found",
                })
        
        # 统计可用快照
        all_snapshots = recovery_engine.snapshot_manager.list_snapshots()
        results["snapshots_available"] = len(all_snapshots)
        
        results["success"] = results["tasks_recovered"] > 0
        
        return results


# ============================================================================
# 快速测试
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("P2 Crash Recovery - 快速验证")
    print("=" * 60)
    
    import tempfile
    from uuid import uuid4
    
    # 创建临时目录
    temp_dir = Path(tempfile.mkdtemp())
    snapshot_dir = temp_dir / "snapshots"
    state_dir = temp_dir / "states"
    
    print(f"\n[OK] 临时目录：{temp_dir}")
    
    # 初始化恢复引擎
    engine = CrashRecoveryEngine(snapshot_dir, state_dir)
    
    # 测试 1: 注册任务（真实状态 + 落盘断言）
    print("\n[OK] 注册任务:")
    task_id = str(uuid4())
    state = asyncio.run(engine.register_task(task_id, total_steps=5))
    print(f"  任务 ID: {task_id}")
    print(f"  总步骤：{state.total_steps}")
    assert state.status == "running" and state.current_step == 0 and state.total_steps == 5, \
        ("注册任务应为 running/step0/total5", state.status, state.current_step, state.total_steps)
    assert (state_dir / f"task_{task_id}.json").exists(), \
        "register_task 必须把状态持久化到磁盘（真实副作用，非仅内存）"

    # 测试 2: 更新进度
    print("\n[OK] 更新进度:")
    asyncio.run(engine.update_task_progress(task_id, current_step=2))
    print(f"  当前步骤：2/5")
    assert engine.task_states[task_id].current_step == 2, \
        "update_task_progress 应真实推进 current_step 到 2"

    # 测试 3: 模拟崩溃（recoverable=True -> interrupted）
    print("\n[OK] 模拟崩溃:")
    asyncio.run(engine.fail_task(task_id, "Simulated crash", recoverable=True))
    print(f"  状态：interrupted")
    _st = engine.task_states[task_id]
    assert _st.status == "interrupted" and _st.error_message == "Simulated crash", \
        ("可恢复失败应置 interrupted 并记录错误", _st.status, _st.error_message)

    # 测试 4: 悬空任务检测（反向哨兵 + 正向哨兵，证明判定非恒定）
    print("\n[OK] 检查悬空任务:")
    orphaned = asyncio.run(engine.check_orphaned_tasks())
    print(f"  发现悬空任务：{len(orphaned)}")
    assert task_id not in {t.task_id for t in orphaned}, \
        "刚失败/心跳新鲜的任务不应被判为悬空（否则检测恒真即伪造）"
    stale_id = str(uuid4())
    asyncio.run(engine.register_task(stale_id, total_steps=3))
    engine.task_states[stale_id].status = "running"
    engine.task_states[stale_id].last_heartbeat = datetime.fromtimestamp(datetime.now().timestamp() - 3600)
    engine.save_task_state(engine.task_states[stale_id])
    orphaned2 = asyncio.run(engine.check_orphaned_tasks())
    assert stale_id in {t.task_id for t in orphaned2}, \
        "心跳过期 1 小时且仍 running 的任务必须被判为悬空（否则检测恒假即伪造）"

    # 测试 5: 恢复任务（无快照时不得崩溃，返回 None 或合法快照）
    print("\n[OK] 恢复任务:")
    snapshot = asyncio.run(engine.recover_task(task_id, RecoveryLevel.TASK_ROLLBACK))
    assert snapshot is None or getattr(snapshot, "id", None), \
        ("recover_task 应返回 None 或带 id 的快照，绝不返回伪对象", snapshot)
    print(f"  恢复结果：{snapshot.id if snapshot else '无可用快照（如实 None）'}")

    # 测试 6: 重启后从磁盘真实恢复 + 恢复报告自洽
    print("\n[OK] 恢复报告:")
    _reloaded = CrashRecoveryEngine(snapshot_dir, state_dir)
    assert _reloaded.task_states[task_id].status == "interrupted" and \
        _reloaded.task_states[task_id].current_step == 2, \
        "新引擎实例须从磁盘读回 interrupted/step2（真实持久化，非进程内假象）"
    report = engine.generate_recovery_report(task_id)
    print(f"  任务状态：{report['status']}")
    print(f"  错误信息：{report['error_message']}")
    print(f"  建议：{len(report['recommendations'])} 条")
    assert report["status"] == "interrupted" and report["current_step"] == 2 and \
        report["error_message"] == "Simulated crash", \
        ("恢复报告须与真实任务状态一致", report["status"], report["current_step"], report["error_message"])
    
    # 测试 7: UNDO（撤销上一步）真实回退一步
    print("\n[TEST] UNDO revert to previous recoverable snapshot:")
    undo_task = str(uuid4())
    undo_doc = temp_dir / "undo_doc.txt"
    asyncio.run(engine.register_task(undo_task, total_steps=3))
    undo_doc.write_bytes(b"STEP-A")
    snap_a = asyncio.run(engine.snapshot_manager.create_snapshot(
        str(undo_doc), snapshot_type="full", metadata={"task_id": undo_task}))
    undo_doc.write_bytes(b"STEP-B")
    snap_b = asyncio.run(engine.snapshot_manager.create_snapshot(
        str(undo_doc), snapshot_type="full", metadata={"task_id": undo_task}))
    # 当前恢复点设为最新快照 snap_b，UNDO 应回退到其前一个 snap_a
    engine.task_states[undo_task].checkpoint_snapshot_id = snap_b.id
    recovered = asyncio.run(engine.recover_task(undo_task, RecoveryLevel.UNDO))
    content_after = undo_doc.read_bytes()
    assert recovered is not None and recovered.id == snap_a.id \
        and content_after == b"STEP-A" \
        and engine.task_states[undo_task].checkpoint_snapshot_id == snap_a.id, \
        ("UNDO 须真实回退到上一快照且写回 STEP-A、checkpoint 前移",
         recovered.id if recovered else None, content_after)
    print(f"  [OK] reverted to {snap_a.id[:8]}, file bytes == STEP-A, checkpoint moved back")

    # 测试 8: 无可撤销的历史时诚实返回 None，绝不伪造/篡改文件
    print("\n[TEST] UNDO honest no-op when nothing earlier to undo:")
    undo_doc.write_bytes(b"STEP-A2")  # 篡改：若不诚实伪造则文件应保持此值
    none_recovered = asyncio.run(engine.recover_task(undo_task, RecoveryLevel.UNDO))
    assert none_recovered is None and undo_doc.read_bytes() == b"STEP-A2", \
        ("无可撤销历史时须返回 None 且不篡改文件",
         none_recovered is not None, undo_doc.read_bytes())
    print("  [OK] returns None and leaves file untouched (no fabricated restore)")
    
    print("\n" + "=" * 60)
    print("[PASS] P2 Crash Recovery 断言自测通过（真实状态机 + 落盘持久化 + 悬空正反向哨兵 + UNDO 真回退）")
    print("=" * 60)
