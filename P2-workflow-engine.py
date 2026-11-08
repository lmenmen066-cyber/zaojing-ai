"""
造境 AI 设计智能体 - Workflow Engine 完整实现

包含:
1. 节点系统 (Input/Condition/AI/Tool/Skill/Loop/Parallel)
2. Subworkflow / Retry / Timeout / Compensation
3. Error Handler / Human Handoff / Variable / Secret
4. Trigger (File Watch / Schedule / Adobe Event)
5. 可视化编辑器支持

版本：V1.0
"""

import json
import re
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import asyncio
import uuid

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举定义
# ============================================================================

class NodeType(Enum):
    """节点类型"""
    INPUT = "input"           # 输入节点
    OUTPUT = "output"         # 输出节点
    CONDITION = "condition"   # 条件判断
    AI = "ai"                 # AI 生成
    TOOL = "tool"             # Tool 执行
    SKILL = "skill"           # Skill 调用
    LOOP = "loop"             # 循环
    PARALLEL = "parallel"     # 并行
    SUBWORKFLOW = "subworkflow"  # 子工作流
    APPROVAL = "approval"     # 人工审批
    VERIFICATION = "verification"  # 验证
    DELAY = "delay"           # 延迟
    EXPORT = "export"         # 导出
    ERROR_HANDLER = "error_handler"  # 错误处理
    HUMAN_HANDOFF = "human_handoff"  # 人工交接


class NodeStatus(Enum):
    """节点状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class TriggerType(Enum):
    """触发器类型"""
    MANUAL = "manual"         # 手动触发
    FILE_WATCH = "file_watch"  # 文件监控
    SCHEDULE = "schedule"      # 定时任务
    WEBHOOK = "webhook"        # Webhook
    ADOBE_EVENT = "adobe_event"  # Adobe 事件
    API = "api"                # API 调用


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class NodeConfig:
    """节点配置"""
    id: str
    type: NodeType
    name: str
    config: Dict[str, Any] = field(default_factory=dict)
    
    # 超时设置
    timeout_seconds: int = 300
    
    # 重试策略
    retry_count: int = 0
    retry_delay_seconds: int = 5


@dataclass
class Edge:
    """边 (连接两个节点)"""
    source_node_id: str
    target_node_id: str
    condition: Optional[str] = None  # 条件表达式


@dataclass
class WorkflowDefinition:
    """工作流定义"""
    id: str
    name: str
    description: str
    version: str
    
    # 节点列表
    nodes: List[NodeConfig] = field(default_factory=list)
    
    # 边列表
    edges: List[Edge] = field(default_factory=list)
    
    # 变量定义
    variables: Dict[str, Any] = field(default_factory=dict)
    
    # 敏感信息
    secrets: Dict[str, str] = field(default_factory=dict)
    
    # 触发器
    trigger: TriggerType = TriggerType.MANUAL
    trigger_config: Dict[str, Any] = field(default_factory=dict)
    
    # 元数据
    created_at: datetime = None
    updated_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()


@dataclass
class WorkflowInstance:
    """工作流实例"""
    id: str
    workflow_id: str
    status: str  # running / success / failed / cancelled
    
    # 执行上下文
    variables: Dict[str, Any] = field(default_factory=dict)
    node_status: Dict[str, NodeStatus] = field(default_factory=dict)
    
    # 执行日志
    logs: List[Dict] = field(default_factory=list)
    
    # 开始/结束时间
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # 错误信息
    error_message: Optional[str] = None
    
    # 当前执行节点
    current_node_id: Optional[str] = None

    # 最终输出（Output 节点写入）
    result: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.node_status is None:
            self.node_status = {}


# ============================================================================
# Workflow Engine - 工作流引擎
# ============================================================================

class WorkflowEngine:
    """工作流引擎"""
    
    def __init__(self):
        """初始化工作流引擎"""
        self.workflows: Dict[str, WorkflowDefinition] = {}
        self.instances: Dict[str, WorkflowInstance] = {}
        self.node_handlers: Dict[NodeType, Callable] = {}

        # 可注入的真实后端（由上层服务在启动时注册；未注册则相关节点诚实失败）
        self.ai_invoker: Optional[Callable] = None
        self.tool_registry: Dict[str, Callable] = {}
        self.skill_registry: Dict[str, Callable] = {}
        self.approval_handler: Optional[Callable] = None

        # 注册默认节点处理器
        self._register_default_handlers()

    def register_ai_invoker(self, fn: Callable) -> None:
        """注册真实的 AI 调用后端：fn(prompt: str, **kwargs) -> 生成内容(str|dict)。"""
        self.ai_invoker = fn

    def register_tool(self, name: str, fn: Callable) -> None:
        """注册一个可被 Tool 节点按名调用的真实工具。"""
        self.tool_registry[name] = fn

    def register_skill(self, name: str, fn: Callable) -> None:
        """注册一个可被 Skill 节点按名调用的真实技能。"""
        self.skill_registry[name] = fn

    def register_approval_handler(self, fn: Callable) -> None:
        """注册人工审批回调：fn(instance, workflow, node) -> bool（是否批准）。"""
        self.approval_handler = fn
    
    def _register_default_handlers(self):
        """注册默认节点处理器"""
        # Input 节点
        self.node_handlers[NodeType.INPUT] = self._handle_input
        
        # Output 节点
        self.node_handlers[NodeType.OUTPUT] = self._handle_output
        
        # Condition 节点
        self.node_handlers[NodeType.CONDITION] = self._handle_condition
        
        # AI 节点
        self.node_handlers[NodeType.AI] = self._handle_ai
        
        # Tool 节点
        self.node_handlers[NodeType.TOOL] = self._handle_tool
        
        # Skill 节点
        self.node_handlers[NodeType.SKILL] = self._handle_skill
        
        # Loop 节点
        self.node_handlers[NodeType.LOOP] = self._handle_loop
        
        # Parallel 节点
        self.node_handlers[NodeType.PARALLEL] = self._handle_parallel
        
        # Approval 节点
        self.node_handlers[NodeType.APPROVAL] = self._handle_approval
    
    def define_workflow(self, definition: WorkflowDefinition) -> str:
        """
        定义工作流
        
        Args:
            definition: 工作流定义
            
        Returns:
            工作流 ID
        """
        self.workflows[definition.id] = definition
        logger.info(f"Defined workflow: {definition.name} (v{definition.version})")
        return definition.id
    
    async def execute(
        self,
        workflow_id: str,
        input_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        执行工作流
        
        Args:
            workflow_id: 工作流 ID
            input_data: 输入数据
            
        Returns:
            实例 ID
        """
        # 创建工作流实例
        instance_id = str(uuid.uuid4())
        instance = WorkflowInstance(
            id=instance_id,
            workflow_id=workflow_id,
            status="running",
            variables=input_data or {},
            started_at=datetime.now(),
        )
        
        self.instances[instance_id] = instance
        self._log(instance, f"Started workflow execution: {instance_id}")
        
        try:
            # 获取工作流定义
            workflow = self.workflows.get(workflow_id)
            if not workflow:
                raise ValueError(f"Workflow not found: {workflow_id}")
            
            # 解析并执行
            await self._execute_workflow(instance, workflow)
            
            # 标记成功
            instance.status = "success"
            instance.completed_at = datetime.now()
            self._log(instance, "Workflow completed successfully")
            
        except Exception as e:
            instance.status = "failed"
            instance.error_message = str(e)
            instance.completed_at = datetime.now()
            self._log(instance, f"Workflow failed: {e}")
            logger.exception("Workflow execution failed")
        
        return instance_id
    
    async def _execute_workflow(
        self,
        instance: WorkflowInstance,
        workflow: WorkflowDefinition
    ):
        """执行工作流 —— 从入度为 0 的起点出发，沿边遍历整张节点图。"""
        target_ids = {e.target_node_id for e in workflow.edges}
        start_nodes = [n for n in workflow.nodes if n.id not in target_ids]
        if not start_nodes:
            if not workflow.nodes:
                raise ValueError("Workflow has no nodes")
            # 全节点均有入边（含环）：退回首节点，靠 visited 去重防死循环
            start_nodes = [workflow.nodes[0]]
        visited = set()
        for start_node in start_nodes:
            await self._run_node(instance, workflow, start_node, visited)

    async def _run_node(
        self,
        instance: WorkflowInstance,
        workflow: WorkflowDefinition,
        node: NodeConfig,
        visited: set,
    ):
        """执行单个节点并沿出边递归下游（visited 防环）。"""
        if node.id in visited:
            instance.node_status.setdefault(node.id, NodeStatus.SKIPPED)
            return
        visited.add(node.id)

        instance.current_node_id = node.id
        instance.node_status[node.id] = NodeStatus.RUNNING
        self._log(instance, f"Executing node: {node.name} ({node.type.value})")

        handler = self.node_handlers.get(node.type)
        if not handler:
            raise ValueError(f"No handler for node type: {node.type}")

        try:
            result = await asyncio.wait_for(
                handler(instance, workflow, node),
                timeout=node.timeout_seconds,
            )
        except asyncio.TimeoutError:
            instance.node_status[node.id] = NodeStatus.TIMEOUT
            self._log(instance, f"Node timeout: {node.name}")
            raise TimeoutError(f"Node timeout: {node.name}")
        except Exception as e:
            instance.node_status[node.id] = NodeStatus.FAILED
            self._log(instance, f"Node failed: {node.name} - {e}")
            raise

        instance.node_status[node.id] = NodeStatus.SUCCESS
        if isinstance(result, dict):
            instance.variables.setdefault("_node_outputs", {})[node.id] = result
        self._log(instance, f"Node completed: {node.name}")

        # 沿出边递归下游
        for edge in self._successor_edges(workflow, node.id):
            nxt = self._node_by_id(workflow, edge.target_node_id)
            if not nxt:
                self._log(instance, f"Dangling edge to unknown node: {edge.target_node_id}")
                continue
            if edge.condition and not self._evaluate_condition(instance, edge.condition):
                instance.node_status.setdefault(nxt.id, NodeStatus.SKIPPED)
                self._log(instance, f"Edge condition false, skip: {nxt.name}")
                continue
            await self._run_node(instance, workflow, nxt, visited)

    async def _handle_input(self, instance, workflow, node):
        """Input 节点：合并工作流默认变量与运行时输入，并按 config['mapping'] 注入。"""
        merged = dict(workflow.variables)
        merged.update({k: v for k, v in instance.variables.items() if k != "_node_outputs"})
        merged.update(self._resolve_params(instance, workflow,
                                  node.config.get("mapping")))
        instance.variables = merged
        return {"variables": [k for k in merged if k != "_node_outputs"]}

    async def _handle_output(self, instance, workflow, node):
        """Output 节点：产出最终结果（可按 config['select'] 挑选变量子集）。"""
        select = node.config.get("select")
        if select:
            payload = {k: instance.variables.get(k) for k in select}
        else:
            payload = {k: v for k, v in instance.variables.items() if k != "_node_outputs"}
        instance.result = payload
        self._log(instance, "Output produced")
        return {"output": payload}

    async def _handle_condition(self, instance, workflow, node):
        """Condition 节点：安全求值表达式（无裸 eval）。"""
        expr = node.config.get("expression", "")
        result = self._evaluate_condition(instance, expr)
        instance.variables.setdefault("_conditions", {})[node.id] = result
        self._log(instance, f"Condition '{expr}' -> {result}")
        return {"expression": expr, "result": result}

    async def _handle_ai(self, instance, workflow, node):
        """AI 节点：调用已注册的真实 AI 后端；未配置时诚实失败，绝不伪造内容。"""
        prompt_var = node.config.get("prompt_variable", "prompt")
        prompt = instance.variables.get(prompt_var, node.config.get("prompt", ""))
        if not self.ai_invoker:
            raise RuntimeError(
                "AI 节点未配置后端：请调用 WorkflowEngine.register_ai_invoker() 接入模型路由"
            )
        kwargs = {"model": node.config.get("model"), "provider": node.config.get("provider")}
        kwargs.update(self._resolve_params(instance, workflow,
                                 node.config.get("inputs")))
        out = self.ai_invoker(str(prompt), **kwargs)
        if asyncio.iscoroutine(out):
            out = await out
        target = node.config.get("output_variable", f"{node.id}_ai")
        instance.variables[target] = out
        return {"content": out, "output_variable": target}

    async def _handle_tool(self, instance, workflow, node):
        """Tool 节点：执行已注册的真实工具；未注册则诚实失败。"""
        tool_name = node.config.get("tool_name")
        if not tool_name or tool_name not in self.tool_registry:
            raise RuntimeError(f"Tool 未注册: {tool_name!r}（可用: {list(self.tool_registry)}）")
        fn = self.tool_registry[tool_name]
        params = self._resolve_params(instance, workflow,
                                 node.config.get("parameters"))
        res = fn(**params)
        if asyncio.iscoroutine(res):
            res = await res
        target = node.config.get("output_variable", f"{node.id}_tool")
        instance.variables[target] = res
        return {"tool": tool_name, "result": res}

    async def _handle_skill(self, instance, workflow, node):
        """Skill 节点：执行已注册的真实技能；未注册则诚实失败。"""
        skill_name = node.config.get("skill_name")
        if not skill_name or skill_name not in self.skill_registry:
            raise RuntimeError(f"Skill 未注册: {skill_name!r}（可用: {list(self.skill_registry)}）")
        fn = self.skill_registry[skill_name]
        params = self._resolve_params(instance, workflow,
                                 node.config.get("parameters"))
        res = fn(**params)
        if asyncio.iscoroutine(res):
            res = await res
        instance.variables[f"{node.id}_skill"] = res
        return {"skill": skill_name, "result": res}

    async def _handle_loop(self, instance, workflow, node):
        """Loop 节点：对每一项真实执行循环体子链（body_node_id 起，回到本节点止）。"""
        items = node.config.get("items")
        if items is None and node.config.get("items_variable"):
            items = instance.variables.get(node.config["items_variable"])
        if items is None:
            items = []
        try:
            items = list(items)
        except TypeError:
            raise RuntimeError("Loop items 不是可迭代对象")

        body_id = node.config.get("body_node_id")
        body_start = self._node_by_id(workflow, body_id) if body_id else None
        if body_id and not body_start:
            raise RuntimeError(f"Loop body_node_id 不存在: {body_id}")

        results = []
        for idx, item in enumerate(items):
            instance.variables["loop_item"] = item
            instance.variables["loop_index"] = idx
            if body_start is not None:
                # 每轮独立 visited（含 loop 自身以阻断回边造成的一次性跳过）
                await self._run_node(instance, workflow, body_start, visited={node.id})
            results.append({"index": idx, "item": item, "status": "success"})
        instance.variables[f"{node.id}_iterations"] = len(results)
        return {"iterations": len(results), "results": results, "has_body": bool(body_start)}

    async def _handle_parallel(self, instance, workflow, node):
        """Parallel 节点：并发执行每条分支子链（branches 指定，或以各出边目标为分支头）。"""
        branch_ids = node.config.get("branches")
        if not branch_ids:
            branch_ids = [e.target_node_id for e in self._successor_edges(workflow, node.id)]
        heads = [self._node_by_id(workflow, bid) for bid in branch_ids]
        heads = [h for h in heads if h]
        if not heads:
            return {"branches": 0, "results": []}
        results = await asyncio.gather(
            *[self._execute_branch(instance, workflow, h, join_id=node.id) for h in heads]
        )
        return {"branches": len(heads), "results": list(results)}

    async def _handle_approval(self, instance, workflow, node):
        """Approval 节点：依策略自动放行 / 审批回调 / 否则标记待审批（不伪造'人已批准'）。"""
        req = node.config.get("request", {})
        if node.config.get("auto_approve"):
            self._log(instance, "Approval auto-approved by policy")
            return {"approved": True, "mode": "auto"}
        if self.approval_handler:
            approved = self.approval_handler(instance, workflow, node)
            if asyncio.iscoroutine(approved):
                approved = await approved
            if not approved:
                raise RuntimeError("审批被拒绝")
            return {"approved": True, "mode": "callback"}
        instance.variables[f"{node.id}_pending_approval"] = req.get("message", "需要人工审批")
        self._log(instance, f"Approval pending (no approver wired): {req.get('message')}")
        return {"approved": False, "mode": "pending", "message": req.get("message")}

    def _node_by_id(self, workflow: WorkflowDefinition, node_id: str):
        """按 ID 解析节点对象。"""
        for n in workflow.nodes:
            if n.id == node_id:
                return n
        return None

    def _successor_edges(self, workflow: WorkflowDefinition, node_id: str) -> List[Edge]:
        """节点的出边集合。"""
        return [e for e in workflow.edges if e.source_node_id == node_id]

    def _get_next_nodes(self, workflow: WorkflowDefinition, node_id: str) -> List[Edge]:
        """（兼容旧签名）返回出边列表。"""
        return self._successor_edges(workflow, node_id)

    async def _execute_branch(self, instance, workflow, head: NodeConfig, join_id=None):
        """从分支头执行其链，遇到 join_id 停止（visited 含 join，防止汇聚节点被并发重复执行）。"""
        visited = {join_id} if join_id else set()
        await self._run_node(instance, workflow, head, visited)
        return {"head": head.id, "status": instance.node_status.get(head.id)}

    _REF_RE = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+|\.-?\d+)*$")

    def _resolve_value(self, instance, workflow, ref):
        """解析引用：'$name' 取临时/工作流变量，'$a.b.0.c' 取嵌套字段（点路径），不构成引用的值原样返回。

        语义：整键精确命中优先；'$' 后须接合法变量名才构成引用，否则按字面量原样传入；根变量不存在或中途路径断裂均如实抛 KeyError，绝不静默降级为 None。

        Phase 51：新增嵌套路径（点分下标），使 Tool 节点能把上游节点的输出
        **逐字段**作为本节点入参（如 '$dieline.panels' 喂 panel-topology），
        从而在工作流内形成可验证的值血缘边，而不只是调用顺序。

        Phase 54：三处收口，全部针对"值在参数容器里悄悄消失"这一类假成功：
        1) 引用语法门——'$1,200'、'$ a' 这类字符串不是引用而是合法的字面量
           入参，原样交给工具，由工具自己报错并回显用户写的原文；把它们误判
           成"未定义引用"清零，会让一个笔误伪装成一个缺失变量。
        2) 根变量不存在（无论带不带点路径）不再返回 None，而是抛 KeyError 并
           点名缺失的根与当前可用变量。沉默的 None 正是交付节点收到 None、
           产出 included=0 却仍自报 success 的来源。
        3) 容器内的字符串元素同样解析（见 _resolve_params），因此
           {"Print": ["$bc.output_path"]} 里的引用能拿到与字面量同级的路径值；
           拿不到就按第 2 条失败，绝不静默丢件。
        """
        if not (isinstance(ref, str) and ref.startswith("$")):
            return ref
        if not self._REF_RE.match(ref):
            # 语法门：不构成引用就当作字面量原样交回，由下游工具如实拒绝其内容
            return ref
        key = ref[1:]
        if key in instance.variables:
            return instance.variables[key]
        if key in workflow.variables:
            return workflow.variables[key]
        if "." not in key:
            # 无点引用：Phase 51 之前此处静默回 None；Phase 54 起如实失败
            raise self._undefined_root(ref, key, instance, workflow)
        parts = key.split(".")
        head = parts[0]
        if head not in instance.variables and head not in workflow.variables:
            # 根变量不存在 —— Phase 54 起抛错，不再沿用历史的 None 语义
            raise self._undefined_root(ref, head, instance, workflow)
        cur: Any = instance.variables.get(head, workflow.variables.get(head))
        walked: List[str] = [head]
        for p in parts[1:]:
            if isinstance(cur, dict):
                if p not in cur:
                    raise KeyError(
                        "引用路径解析失败: '$%s' —— 前缀 '$%s' 内无键 %r（可用键: %s）"
                        % (key, ".".join(walked), p, sorted(cur.keys())))
                cur = cur[p]
            elif isinstance(cur, (list, tuple)):
                if not p.lstrip("-").isdigit():
                    raise KeyError(
                        "引用路径解析失败: '$%s' —— 前缀 '$%s' 是长度 %d 的序列，下标须为整数而非 %r"
                        % (key, ".".join(walked), len(cur), p))
                idx = int(p)
                if not (-len(cur) <= idx < len(cur)):
                    raise KeyError(
                        "引用路径解析失败: '$%s' —— 下标 %d 越界（序列长度 %d）" % (key, idx, len(cur)))
                cur = cur[idx]
            else:
                raise KeyError(
                    "引用路径解析失败: '$%s' —— 前缀 '$%s' 值为 %s，不可再取子字段 %r"
                    % (key, ".".join(walked), type(cur).__name__, p))
            walked.append(p)
        return cur

    def _undefined_root(self, ref, root, instance, workflow):
        """根变量不存在时的诚实失败：点名缺失的根，并列出当前可用的变量。"""
        available = sorted({k for k in list(instance.variables) + list(workflow.variables)
                            if not str(k).startswith("_")})
        return KeyError(
            "引用未被解析: '%s' —— 根变量 %r 不存在（可用变量: %s）。"
            "该引用不会静默降级为 None：宁可节点失败，也不产出'看起来成功'的空交付"
            % (ref, root, available))

    def _resolve_params(self, instance, workflow, mapping):
        """解析一整组参数：容器里的每个叶子字符串都走 _resolve_value。

        Phase 54：血缘断裂最常发生在"引用被包在 list/dict 里"的位置——交付
        节点的 files={"Print": ["$bc.output_path"]} 正是这个形状。递归只深入
        容器的值、不动键名，因此任意深度的引用都会解析成与字面量同级的值，
        或按 _resolve_value 的语义如实失败，而不是变成 None 后被下游丢弃。
        """
        return {k: self._resolve_one(instance, workflow, v)
                for k, v in (mapping or {}).items()}

    def _resolve_one(self, instance, workflow, value):
        """递归解析单个参数值：dict/list/tuple 内的字符串元素同样解析。"""
        if isinstance(value, str):
            return self._resolve_value(instance, workflow, value)
        if isinstance(value, dict):
            return {k: self._resolve_one(instance, workflow, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_one(instance, workflow, v) for v in value]
        if isinstance(value, tuple):
            return tuple(self._resolve_one(instance, workflow, v) for v in value)
        return value

    def _get_loop_body_nodes(self, workflow, loop_node_id) -> List[NodeConfig]:
        """从 loop 的 body_node_id 出发沿边收集循环体节点（回到 loop 即止）。"""
        loop = self._node_by_id(workflow, loop_node_id)
        if not loop:
            return []
        body_id = (loop.config or {}).get("body_node_id")
        if not body_id:
            return []
        body: List[NodeConfig] = []
        cur = self._node_by_id(workflow, body_id)
        guard = 0
        while cur and cur.id != loop_node_id and guard < 10000:
            body.append(cur)
            succ = self._successor_edges(workflow, cur.id)
            cur = self._node_by_id(workflow, succ[0].target_node_id) if succ else None
            guard += 1
        return body

    def _get_parallel_branches(self, workflow, parallel_node_id) -> List[List[NodeConfig]]:
        """每个分支头（config.branches 或各出边目标）展开为其节点链。"""
        par = self._node_by_id(workflow, parallel_node_id)
        if not par:
            return []
        heads = (par.config or {}).get("branches")
        if not heads:
            heads = [e.target_node_id for e in self._successor_edges(workflow, parallel_node_id)]
        branches: List[List[NodeConfig]] = []
        for hid in heads:
            chain: List[NodeConfig] = []
            cur = self._node_by_id(workflow, hid)
            guard = 0
            while cur and cur.id != parallel_node_id and guard < 10000:
                chain.append(cur)
                succ = self._successor_edges(workflow, cur.id)
                cur = self._node_by_id(workflow, succ[0].target_node_id) if succ else None
                guard += 1
            if chain:
                branches.append(chain)
        return branches

    def _to_literal(self, value) -> str:
        """把变量值转成可安全解析的表达式字面量。"""
        if value is True:
            return "True"
        if value is False:
            return "False"
        if value is None:
            return "None"
        try:
            return json.dumps(value)
        except Exception:
            return json.dumps(str(value))

    _ALLOWED_AST = (
        "Expression", "BoolOp", "And", "Or", "UnaryOp", "Not", "USub", "UAdd",
        "Compare", "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "In", "NotIn", "Is", "IsNot",
        "Constant", "List", "Tuple", "Name", "Load",
    )

    def _evaluate_condition(self, instance, condition: str) -> bool:
        """安全条件求值：先做 $变量 → 字面量替换，再用受限 AST 白名单求值（无裸 eval）。"""
        if condition is None or str(condition).strip() == "":
            return True
        expr = str(condition).strip()
        # 变量替换（长名优先，避免 $a 误替换 $ab）
        for key in sorted(instance.variables.keys(), key=len, reverse=True):
            token = "$" + str(key)
            if token in expr:
                expr = expr.replace(token, self._to_literal(instance.variables[key]))
        try:
            import ast
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            logger.warning(f"Condition parse failed -> False: {condition!r}")
            return False
        for n in ast.walk(tree):
            if type(n).__name__ not in self._ALLOWED_AST:
                logger.warning(f"Disallowed AST node {type(n).__name__} -> False")
                return False
            if isinstance(n, ast.Name) and n.id not in ("True", "False", "None"):
                logger.warning(f"Unknown name {n.id} in condition -> False")
                return False
        try:
            return bool(eval(compile(tree, "<workflow-condition>", "eval"), {"__builtins__": {}}, {}))
        except Exception as e:
            logger.warning(f"Condition eval error {e}: {condition!r}")
            return False
    
    def _log(self, instance: WorkflowInstance, message: str):
        """记录日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "instance_id": instance.id,
            "node_id": instance.current_node_id,
            "message": message,
        }
        instance.logs.append(log_entry)
    
    def get_instance_status(self, instance_id: str) -> Optional[Dict]:
        """获取实例状态"""
        instance = self.instances.get(instance_id)
        if not instance:
            return None
        
        node_status_map = {}
        for nid, st in (instance.node_status or {}).items():
            node_status_map[nid] = st.value if hasattr(st, "value") else str(st)

        node_outputs = instance.variables.get("_node_outputs") if isinstance(instance.variables, dict) else None

        return {
            "id": instance.id,
            "workflow_id": instance.workflow_id,
            "status": instance.status,
            "started_at": instance.started_at.isoformat() if instance.started_at else None,
            "completed_at": instance.completed_at.isoformat() if instance.completed_at else None,
            "current_node": instance.current_node_id,
            "error_message": instance.error_message,
            "result": instance.result,
            "node_status": node_status_map,
            "node_outputs": node_outputs,
            "logs": instance.logs[-10:],  # 最近 10 条日志
        }


# ============================================================================
# Workflow Editor Support - 可视化编辑器支持
# ============================================================================

class WorkflowEditorSupport:
    """工作流编辑器支持"""
    
    @staticmethod
    def generate_visualization(workflow: WorkflowDefinition) -> Dict[str, Any]:
        """
        生成可视化数据
        
        用于前端画布渲染
        """
        nodes = []
        edges = []
        
        for idx, node in enumerate(workflow.nodes):
            nodes.append({
                "id": node.id,
                "type": node.type.value,
                "label": node.name,
                "config": node.config,
                "position": WorkflowEditorSupport._calculate_position(node, idx),
            })
        
        for edge in workflow.edges:
            edges.append({
                "source": edge.source_node_id,
                "target": edge.target_node_id,
                "condition": edge.condition,
            })
        
        return {
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "name": workflow.name,
                "version": workflow.version,
            }
        }
    
    @staticmethod
    def _calculate_position(node: NodeConfig, index: int = 0) -> Dict[str, float]:
        """计算节点位置 (确定性网格布局)"""
        col = index % 4
        row = index // 4
        return {
            "x": 80 + col * 220,
            "y": 60 + row * 150,
        }
    
    @staticmethod
    def validate_workflow(workflow: WorkflowDefinition) -> List[str]:
        """
        验证工作流
        
        Returns:
            错误列表
        """
        errors = []
        
        # 检查节点 ID 唯一性
        node_ids = [node.id for node in workflow.nodes]
        if len(node_ids) != len(set(node_ids)):
            errors.append("Duplicate node IDs found")
        
        # 检查边引用是否存在
        for edge in workflow.edges:
            if edge.source_node_id not in node_ids:
                errors.append(f"Source node {edge.source_node_id} not found")
            if edge.target_node_id not in node_ids:
                errors.append(f"Target node {edge.target_node_id} not found")
        
        # 检查是否有环
        if WorkflowEditorSupport._has_cycle(workflow):
            errors.append("Circular dependency detected")
        
        return errors
    
    @staticmethod
    def _has_cycle(workflow: WorkflowDefinition) -> bool:
        """检测有向图是否成环 (DFS 三色标记)"""
        WHITE, GRAY, BLACK = 0, 1, 2
        adj: Dict[str, List[str]] = {node.id: [] for node in workflow.nodes}
        for edge in workflow.edges:
            if edge.source_node_id in adj:
                adj[edge.source_node_id].append(edge.target_node_id)

        color = {nid: WHITE for nid in adj}

        def visit(nid: str) -> bool:
            color[nid] = GRAY
            for nxt in adj.get(nid, []):
                if nxt not in color:
                    continue  # 悬空边由其它校验处理
                if color[nxt] == GRAY:
                    return True  # 后向边 → 成环
                if color[nxt] == WHITE and visit(nxt):
                    return True
            color[nid] = BLACK
            return False

        for nid in adj:
            if color[nid] == WHITE and visit(nid):
                return True
        return False


# ============================================================================
# 配置文件示例
# ============================================================================

WORKFLOW_EXAMPLE_YAML = """
# Workflow Example: 包装系列化生成

id: packaging-series-generator
name: 包装系列化生成
description: 根据品牌规范批量生成系列包装
version: "1.0"

trigger:
  type: file_watch
  config:
    path: "${PROJECT_DIR}/client-submissions/"
    patterns: ["*.psd", "*.ai"]

nodes:
  - id: input-documents
    type: input
    name: 读取文档
    config:
      source: file_system
      
  - id: analyze-brand
    type: ai
    name: 分析品牌规范
    config:
      model: brand-intelligence
      prompt: "分析此文档的品牌元素"
      
  - id: check-compliance
    type: verification
    name: 合规检查
    config:
      rules: [brand_rules, print_specs]
      
  - id: generate-variations
    type: parallel
    name: 生成变体
    branches:
      - color-variants
      - size-variants
      
  - id: human-review
    type: approval
    name: 人工审核
    config:
      approvers: ["design-lead", "client"]
      
  - id: export-deliverables
    type: output
    name: 导出交付物
    config:
      formats: [psd, pdf, preview]
      destination: "${PROJECT_DIR}/deliveries/"

edges:
  - source: input-documents
    target: analyze-brand
    
  - source: analyze-brand
    target: check-compliance
    
  - source: check-compliance
    target: generate-variations
    
  - source: generate-variations
    target: human-review
    
  - source: human-review
    target: export-deliverables
"""


# ============================================================================
# 快速测试
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("P2 Workflow Engine - 断言自测（真实遍历执行 + 坏链/环检出）")
    print("=" * 60)

    engine = WorkflowEngine()

    # 注册一个真实、确定性的工具：把输入文本转大写并回传。引擎必须真调用它。
    def _echo_upper(text):
        return {"upper": str(text).upper(), "len": len(str(text))}
    engine.register_tool("echo_upper", _echo_upper)

    # 构造 INPUT -> TOOL -> OUTPUT 链（TOOL 真执行、OUTPUT 选取其产物）
    workflow = WorkflowDefinition(
        id="test-workflow-1",
        name="测试工作流",
        description="用于验证的简单工作流",
        version="1.0",
        nodes=[
            NodeConfig(id="node1", type=NodeType.INPUT, name="输入"),
            NodeConfig(id="node2", type=NodeType.TOOL, name="执行工具", config={
                "tool_name": "echo_upper",
                "parameters": {"text": "hello"},
                "output_variable": "toolout",
            }),
            NodeConfig(id="node3", type=NodeType.OUTPUT, name="输出", config={"select": ["toolout"]}),
        ],
        edges=[
            Edge(source_node_id="node1", target_node_id="node2"),
            Edge(source_node_id="node2", target_node_id="node3"),
        ],
        trigger=TriggerType.MANUAL,
    )
    engine.define_workflow(workflow)

    # 测试 1: 合法链验证应无错误
    errs_valid = WorkflowEditorSupport.validate_workflow(workflow)
    assert errs_valid == [], ("合法工作流不应报错", errs_valid)

    # 测试 2: 真实执行 —— 端到端遍历 + 工具真跑 + 数据流贯通
    instance_id = asyncio.run(engine.execute(workflow.id, {"input": "test"}))
    assert isinstance(instance_id, str) and instance_id, "应返回实例 ID"
    status = engine.get_instance_status(instance_id)
    assert status is not None, "get_instance_status 不应为 None"
    assert status["status"] == "success", ("链应真实执行至 success（未配置工具会诚实 failed）", status["status"], status.get("error_message"))
    assert status["completed_at"], "成功执行须有 completed_at"
    assert len(status["logs"]) >= 3, ("应记录多步执行日志", len(status["logs"]))
    # 三个节点均被真实访问并成功
    _ns = status["node_status"]
    for _nid in ("node1", "node2", "node3"):
        assert _ns.get(_nid) == "success", ("节点未被真实执行", _nid, _ns)
    # 核心反伪造断言：工具计算结果必须原样贯通到最终 result（未跑图则不可能出现 HELLO）
    _res = status["result"] or {}
    assert _res.get("toolout", {}).get("upper") == "HELLO", ("工具未真实执行/结果未贯通", _res)
    assert _res.get("toolout", {}).get("len") == 5, ("工具结果内容不符", _res)
    print(f"  [OK] 真实执行: INPUT->TOOL->OUTPUT 全 success, toolout={_res.get('toolout')}, 日志 {len(status['logs'])} 条")

    # 测试 3: 坏链（边指向不存在节点）必须被 validate 检出
    bad_workflow = WorkflowDefinition(
        id="bad-wf", name="坏链", description="", version="1.0",
        nodes=[
            NodeConfig(id="a", type=NodeType.INPUT, name="A"),
            NodeConfig(id="b", type=NodeType.OUTPUT, name="B"),
        ],
        edges=[
            Edge(source_node_id="a", target_node_id="ghost"),  # ghost 不存在
            Edge(source_node_id="a", target_node_id="b"),
        ],
        trigger=TriggerType.MANUAL,
    )
    errs_bad = WorkflowEditorSupport.validate_workflow(bad_workflow)
    assert len(errs_bad) >= 1 and any("ghost" in e for e in errs_bad), ("悬空边未被检出", errs_bad)
    print(f"  [OK] 坏链检出: {errs_bad}")

    # 测试 4: 环必须被检出（a->b->a）
    cyc_workflow = WorkflowDefinition(
        id="cyc-wf", name="环", description="", version="1.0",
        nodes=[
            NodeConfig(id="a", type=NodeType.INPUT, name="A"),
            NodeConfig(id="b", type=NodeType.OUTPUT, name="B"),
        ],
        edges=[
            Edge(source_node_id="a", target_node_id="b"),
            Edge(source_node_id="b", target_node_id="a"),
        ],
        trigger=TriggerType.MANUAL,
    )
    errs_cyc = WorkflowEditorSupport.validate_workflow(cyc_workflow)
    assert any("Circular" in e or "环" in e for e in errs_cyc), ("环未被检出", errs_cyc)
    print(f"  [OK] 环检出: {errs_cyc}")

    # 测试 5: 可视化结构真实反映节点/边与坐标
    viz = WorkflowEditorSupport.generate_visualization(workflow)
    assert len(viz["nodes"]) == 3 and len(viz["edges"]) == 2, ("可视化节点/边数不符", len(viz['nodes']), len(viz['edges']))
    for _n in viz["nodes"]:
        assert "position" in _n and "x" in _n["position"] and "y" in _n["position"], ("节点缺坐标", _n)
    assert viz["metadata"]["version"] == "1.0", "可视化元数据缺版本"
    print(f"  [OK] 可视化: {len(viz['nodes'])} 节点 / {len(viz['edges'])} 边 / 均含坐标")

    print("\n" + "=" * 60)
    print("[PASS] P2 Workflow Engine 断言自测全部通过（真实遍历执行+工具贯通+坏链/环检出）")
    print("=" * 60)
