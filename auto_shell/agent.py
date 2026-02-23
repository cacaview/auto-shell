"""
Agent 模块
实现 ReAct 循环和多步执行
"""

import re
import json
import uuid
import asyncio
import subprocess
import logging
from typing import Optional, List, Dict, Any, Callable, Awaitable
from pydantic import BaseModel, Field
from enum import Enum
from pathlib import Path
from datetime import datetime, timedelta

from .config import get_config
from .llm_client import get_llm_client, LLMClient

logger = logging.getLogger("auto-shell-agent")


class AgentMode(str, Enum):
    """Agent 运行模式"""
    DEFAULT = "default"      # 每次命令都需要用户确认
    AUTO = "auto"            # AI 自动判断安全性，高危命令需确认
    FULL_AUTO = "full_auto"  # 无安全拦截，允许所有命令


class ActionResult(BaseModel):
    """动作执行结果"""
    action: str
    command: str = ""  # 执行的命令
    success: bool = False
    output: str = ""
    error: str = ""
    needs_confirmation: bool = False
    is_dangerous: bool = False


class AgentState(BaseModel):
    """Agent 状态"""
    mode: AgentMode = AgentMode.DEFAULT
    iteration: int = 0
    max_iterations: int = 10
    history: List[Dict[str, str]] = Field(default_factory=list)
    task_complete: bool = False
    final_message: str = ""


class Agent:
    """智能代理"""
    
    def __init__(
        self,
        mode: AgentMode = AgentMode.DEFAULT,
        on_command: Optional[Callable[[str, ActionResult], Awaitable[bool]]] = None
    ):
        self.config = get_config()
        self.llm = get_llm_client()
        self.state = AgentState(
            mode=mode,
            max_iterations=self.config.agent.max_iterations
        )
        self.on_command = on_command  # 命令执行前的回调
    
    def is_safe_command(self, command: str) -> bool:
        """检查命令是否安全"""
        # 检查危险命令
        for pattern in self.config.agent.dangerous_commands:
            if re.search(pattern, command):
                return False
        return True
    
    def is_dangerous_command(self, command: str) -> bool:
        """检查命令是否危险"""
        for pattern in self.config.agent.dangerous_commands:
            if re.search(pattern, command):
                return True
        return False
    
    def needs_confirmation(self, command: str) -> bool:
        """判断命令是否需要用户确认"""
        if self.state.mode == AgentMode.FULL_AUTO:
            return False
        elif self.state.mode == AgentMode.AUTO:
            return self.is_dangerous_command(command)
        else:  # DEFAULT
            return True
    
    async def execute_command(self, command: str) -> ActionResult:
        """执行命令"""
        result = ActionResult(
            action="execute",
            command=command,
            needs_confirmation=self.needs_confirmation(command),
            is_dangerous=self.is_dangerous_command(command)
        )
        
        # 如果需要确认，调用回调
        if result.needs_confirmation and self.on_command:
            confirmed = await self.on_command(command, result)
            if not confirmed:
                result.success = False
                result.error = "用户取消执行"
                return result
        
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True
            )
            stdout, stderr = await proc.communicate()
            
            result.success = proc.returncode == 0
            result.output = stdout.decode('utf-8', errors='replace')[:2000]
            result.error = stderr.decode('utf-8', errors='replace')[:1000]
            
        except Exception as e:
            result.success = False
            result.error = str(e)
        
        return result
    
    async def read_file(self, path: str) -> ActionResult:
        """读取文件"""
        result = ActionResult(action="read_file")
        
        try:
            file_path = Path(path).expanduser().resolve()
            if not file_path.exists():
                result.success = False
                result.error = f"文件不存在: {file_path}"
                return result
            
            content = file_path.read_text(encoding='utf-8', errors='replace')
            result.success = True
            result.output = content[:5000]  # 限制长度
            
        except Exception as e:
            result.success = False
            result.error = str(e)
        
        return result
    
    async def write_file(self, path: str, content: str) -> ActionResult:
        """写入文件"""
        result = ActionResult(action="write_file")
        
        # 写入文件需要确认
        result.needs_confirmation = True
        result.is_dangerous = True
        
        if self.on_command:
            confirmed = await self.on_command(f"write_file({path})", result)
            if not confirmed:
                result.success = False
                result.error = "用户取消写入"
                return result
        
        try:
            file_path = Path(path).expanduser().resolve()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding='utf-8')
            result.success = True
            result.output = f"已写入 {len(content)} 字符到 {file_path}"
            
        except Exception as e:
            result.success = False
            result.error = str(e)
        
        return result
    
    async def run(
        self,
        query: str,
        context: Dict[str, Any]
    ) -> List[ActionResult]:
        """运行 Agent 循环"""
        results = []
        self.state = AgentState(
            mode=self.state.mode,
            max_iterations=self.config.agent.max_iterations
        )
        
        while not self.state.task_complete and self.state.iteration < self.state.max_iterations:
            self.state.iteration += 1
            logger.info(f"Agent iteration {self.state.iteration}")
            
            # 获取下一个动作
            action = await self.llm.generate_agent_action(
                query=query,
                context=context,
                history=self.state.history
            )
            
            logger.info(f"Agent action: {action}")
            
            # 执行动作
            result = await self._execute_action(action, context)
            results.append(result)
            
            # 记录到历史
            self.state.history.append({
                "role": "assistant",
                "content": json.dumps(action, ensure_ascii=False)
            })
            self.state.history.append({
                "role": "user",
                "content": f"执行结果: {result.output or result.error}"
            })
            
            # 检查是否完成
            if action.get("action") == "done":
                self.state.task_complete = True
                self.state.final_message = action.get("message", "任务完成")
            
            # 如果动作执行失败且是错误，停止循环
            if action.get("action") == "error":
                break
        
        return results
    
    async def _execute_action(
        self,
        action: Dict[str, Any],
        context: Dict[str, Any]
    ) -> ActionResult:
        """执行单个动作"""
        action_type = action.get("action", "unknown")
        
        if action_type == "execute":
            command = action.get("command", "")
            if not command:
                return ActionResult(action="execute", success=False, error="空命令")
            return await self.execute_command(command)
        
        elif action_type == "read_file":
            path = action.get("path", "")
            if not path:
                return ActionResult(action="read_file", success=False, error="未指定文件路径")
            return await self.read_file(path)
        
        elif action_type == "write_file":
            path = action.get("path", "")
            content = action.get("content", "")
            if not path:
                return ActionResult(action="write_file", success=False, error="未指定文件路径")
            return await self.write_file(path, content)
        
        elif action_type == "ask_user":
            # 向用户提问，等待回答
            question = action.get("question", "")
            return ActionResult(
                action="ask_user",
                success=True,
                output=question,
                needs_confirmation=True
            )
        
        elif action_type == "done":
            return ActionResult(
                action="done",
                success=True,
                output=action.get("message", "任务完成")
            )
        
        elif action_type == "error":
            # LLM 生成动作时出错
            return ActionResult(
                action="error",
                success=False,
                error=action.get("message", "LLM 生成动作失败"),
            )
        
        else:
            return ActionResult(
                action="unknown",
                success=False,
                error=f"未知动作类型: {action_type}"
            )

    async def run_one_step(
        self,
        query: str,
        context: Dict[str, Any],
        history: List[Dict[str, str]] = None,
        user_reply: Optional[str] = None,
    ) -> tuple[ActionResult, List[Dict[str, str]]]:
        """运行 Agent 的单个步骤，返回 (结果, 新历史)。
        
        Args:
            query: 用户任务描述
            context: 终端上下文
            history: 对话历史（可以跨 HTTP 请求持久化）
            user_reply: 上一步执行结果（人类消息形式）
        
        Returns:
            (ActionResult, updated_history)
        """
        if history is None:
            history = []

        # 若有上一步结果，添加到历史
        if user_reply:
            history = history + [{"role": "user", "content": user_reply}]

        action = await self.llm.generate_agent_action(
            query=query,
            context=context,
            history=history,
        )

        logger.info(f"Agent one_step action: {action}")
        result = await self._execute_action(action, context)

        # 将本轮 AI 应答追加到历史
        new_history = history + [
            {"role": "assistant", "content": json.dumps(action, ensure_ascii=False)},
        ]

        # 若任务完成，记录状态
        if action.get("action") == "done":
            self.state.task_complete = True
            self.state.final_message = action.get("message", "任务完成")

        self.state.iteration += 1
        return result, new_history


# ============================================================
# Agent Session — 跨 HTTP 请求的持久化会话
# ============================================================

class AgentSession(BaseModel):
    """持久化 Agent 会话"""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task: str
    context: Dict[str, Any] = Field(default_factory=dict)
    mode: AgentMode = AgentMode.DEFAULT
    history: List[Dict[str, str]] = Field(default_factory=list)
    iteration: int = 0
    max_iterations: int = 10
    task_complete: bool = False
    final_message: str = ""
    last_result: Optional[Dict[str, Any]] = None  # 最近一步结果（JSON 格式）
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    class Config:
        arbitrary_types_allowed = True


class AgentSessionManager:
    """Agent 会话管理器（内存存储，进程生命周期内有效）"""

    # 会话超时时间
    SESSION_TTL = timedelta(hours=2)

    def __init__(self):
        self._sessions: Dict[str, AgentSession] = {}

    def create(
        self,
        task: str,
        context: Dict[str, Any],
        mode: AgentMode = AgentMode.DEFAULT,
        max_iterations: int = 10,
    ) -> AgentSession:
        session = AgentSession(
            task=task,
            context=context,
            mode=mode,
            max_iterations=max_iterations,
        )
        self._sessions[session.session_id] = session
        self._cleanup()
        return session

    def get(self, session_id: str) -> Optional[AgentSession]:
        return self._sessions.get(session_id)

    def update(self, session: AgentSession):
        session.updated_at = datetime.now()
        self._sessions[session.session_id] = session

    def delete(self, session_id: str):
        self._sessions.pop(session_id, None)

    def list_sessions(self) -> List[AgentSession]:
        return list(self._sessions.values())

    def _cleanup(self):
        """清理过期会话"""
        now = datetime.now()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - s.updated_at > self.SESSION_TTL
        ]
        for sid in expired:
            del self._sessions[sid]

    async def advance(
        self,
        session_id: str,
        last_command_result: Optional[str] = None,
    ) -> tuple[Optional[AgentSession], Optional[ActionResult]]:
        """推进会话一步（执行下一个 Agent 动作）。

        Args:
            session_id: 会话 ID
            last_command_result: 上一步命令的执行结果（来自 shell），如 "exit_code=0, stdout=..."
        
        Returns:
            (updated_session, action_result)，若会话不存在返回 (None, None)
        """
        session = self.get(session_id)
        if session is None:
            return None, None

        if session.task_complete or session.iteration >= session.max_iterations:
            return session, None

        config = get_config()
        agent = Agent(mode=session.mode)

        result, new_history = await agent.run_one_step(
            query=session.task,
            context=session.context,
            history=session.history,
            user_reply=last_command_result,
        )

        # 持久化更新
        session.history = new_history
        session.iteration += 1
        session.task_complete = agent.state.task_complete
        session.final_message = agent.state.final_message
        session.last_result = result.model_dump()
        self.update(session)

        return session, result


# ============================================================
# 任务复杂度分析 — 智能路由
# ============================================================

# 多步任务关键词（启发式）
MULTI_STEP_KEYWORDS = [
    "然后", "并且", "接着", "之后", "同时", "最后",
    "分析.*并", "找出.*并", "创建.*并", "安装.*并", "压缩.*并",
    "批量", "所有.*文件", "递归", "目录树",
    "部署", "搭建", "配置.*项目", "初始化.*项目",
    "监控", "持续", "循环",
    "and then", "after that", "then", "finally",
    "batch", "all files", "recursive", "deploy", "setup",
]


async def analyze_task_complexity(query: str) -> bool:
    """分析任务是否需要 Agent 模式（多步执行）。

    先用关键词启发，再可选调用 LLM 二次确认（当前：仅关键词）。
    返回 True 表示应使用 Agent 模式。
    """
    query_lower = query.lower()

    # 启发式关键词检测
    for kw in MULTI_STEP_KEYWORDS:
        if re.search(kw, query_lower):
            logger.info(f"Query classified as multi-step by keyword: {kw!r}")
            return True

    # 字符数阈值：过长的描述通常是复杂任务
    if len(query) > 80:
        logger.info("Query classified as multi-step by length")
        return True

    return False


# 便捷函数
async def run_agent(
    query: str,
    context: Dict[str, Any],
    mode: AgentMode = AgentMode.DEFAULT,
    on_command: Optional[Callable[[str, ActionResult], Awaitable[bool]]] = None
) -> List[ActionResult]:
    """运行 Agent（旧接口，一次性完整执行）"""
    agent = Agent(mode=mode, on_command=on_command)
    return await agent.run(query, context)


# 全局 Session Manager
_session_manager: Optional[AgentSessionManager] = None


def get_session_manager() -> AgentSessionManager:
    """获取全局 Agent Session 管理器"""
    global _session_manager
    if _session_manager is None:
        _session_manager = AgentSessionManager()
    return _session_manager

