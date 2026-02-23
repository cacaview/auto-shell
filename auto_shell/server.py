"""
Daemon 服务器模块
提供 HTTP API 供 Shell 插件调用
"""

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
from datetime import datetime
import asyncio

from .config import get_config, reload_config
from .llm_client import get_llm_client
from .agent import (
    Agent, AgentMode, ActionResult, run_agent,
    AgentSession, AgentSessionManager, get_session_manager,
    analyze_task_complexity,
)
from .context import get_collector

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("auto-shell-daemon")

# 创建 FastAPI 应用
app = FastAPI(
    title="auto-shell Daemon",
    description="AI 驱动的 Shell 命令生成服务",
    version="0.1.0"
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============== 请求/响应模型 ==============

class SuggestionRequest(BaseModel):
    """命令建议请求"""
    query: str
    cwd: str = ""
    os: str = "Linux"
    shell: str = "zsh"
    last_command: Optional[str] = None
    last_exit_code: Optional[int] = None


class SuggestionResponse(BaseModel):
    """命令建议响应"""
    command: str
    explanation: str = ""
    is_dangerous: bool = False
    use_agent: bool = False  # True: 建议切换到 Agent 模式


class AgentRequest(BaseModel):
    """Agent 请求"""
    query: str
    cwd: str = ""
    os: str = "Linux"
    shell: str = "zsh"
    mode: str = "default"  # default, auto, full_auto
    auto_confirm: bool = False  # 自动确认所有命令（用于测试）


class AgentStepResponse(BaseModel):
    """Agent 单步响应"""
    iteration: int
    action: str
    command: Optional[str] = None
    success: bool
    output: str = ""
    error: str = ""
    needs_confirmation: bool = False
    is_dangerous: bool = False


class AgentResponse(BaseModel):
    """Agent 完整响应"""
    success: bool
    message: str = ""
    steps: List[AgentStepResponse] = Field(default_factory=list)


class CommandResultRequest(BaseModel):
    """命令执行结果上报"""
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""


# ---- Agent Session 模型 ----

class AgentSessionStartRequest(BaseModel):
    """启动 Agent 会话请求"""
    task: str
    cwd: str = ""
    os: str = "Linux"
    shell: str = "zsh"
    mode: str = "default"  # default, auto, full_auto


class AgentSessionStepRequest(BaseModel):
    """推进 Agent 会话一步请求"""
    session_id: str
    # 上一步在 shell 中实际执行的结果（空字符串表示未执行）
    last_exit_code: Optional[int] = None
    last_stdout: str = ""
    last_stderr: str = ""
    last_command: str = ""


class AgentSessionStepResult(BaseModel):
    """会话单步响应"""
    session_id: str
    iteration: int
    action: str
    command: Optional[str] = None  # 仅 execute 类型有值
    output: str = ""
    error: str = ""
    explanation: str = ""
    is_dangerous: bool = False
    needs_confirmation: bool = False
    task_complete: bool = False
    final_message: str = ""


class AgentSessionStatusResponse(BaseModel):
    """会话状态响应"""
    session_id: str
    task: str
    mode: str
    iteration: int
    max_iterations: int
    task_complete: bool
    final_message: str
    history_length: int


class ConfigResponse(BaseModel):
    """配置响应"""
    llm_api_base: str
    llm_model: str
    daemon_host: str
    daemon_port: int
    agent_mode: str


# ============== API 端点 ==============

@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/config", response_model=ConfigResponse)
async def get_config_info():
    """获取当前配置"""
    config = get_config()
    return ConfigResponse(
        llm_api_base=config.llm.api_base,
        llm_model=config.llm.model,
        daemon_host=config.daemon.host,
        daemon_port=config.daemon.port,
        agent_mode=config.agent.default_mode
    )


@app.post("/config/reload")
async def reload_config_endpoint():
    """重新加载配置"""
    reload_config()
    return {"status": "ok", "message": "配置已重新加载"}


@app.post("/v1/suggest", response_model=SuggestionResponse)
async def get_suggestion(request: SuggestionRequest):
    """获取命令建议（含智能路由：复杂任务返回 use_agent=True）"""
    logger.info(f"Received suggestion request: {request.query} in {request.cwd}")
    
    config = get_config()
    llm = get_llm_client()
    
    # 智能路由：判断任务复杂度
    use_agent = await analyze_task_complexity(request.query)
    if use_agent:
        logger.info(f"Query routed to Agent mode: {request.query}")
        return SuggestionResponse(
            command="",
            explanation="此任务需要多个步骤，建议使用 Agent 模式（双击 Tab 触发）",
            is_dangerous=False,
            use_agent=True,
        )
    
    # 构造上下文
    context = {
        "cwd": request.cwd or ".",
        "os": request.os,
        "shell": request.shell,
        "last_command": request.last_command,
        "last_exit_code": request.last_exit_code,
    }
    
    # 调用 LLM 生成命令
    command = await llm.generate_command(request.query, context)
    
    if not command:
        command = "echo 'auto-shell: 无法生成命令，请检查 API 配置或网络连接'"
    
    # 检查命令是否危险
    is_dangerous = any(
        pattern in command 
        for pattern in ["rm ", "sudo ", "chmod ", "chown ", "> ", "mkfs", "dd "]
    )
    
    return SuggestionResponse(
        command=command,
        explanation="Generated by LLM.",
        is_dangerous=is_dangerous,
        use_agent=False,
    )


@app.post("/v1/suggest/stream")
async def get_suggestion_stream(request: SuggestionRequest):
    """流式获取命令建议 (SSE)"""
    from fastapi.responses import StreamingResponse
    import json
    
    config = get_config()
    llm = get_llm_client()
    
    context = {
        "cwd": request.cwd or ".",
        "os": request.os,
        "shell": request.shell,
    }
    
    async def generate():
        async for chunk in llm.stream_command(request.query, context):
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream"
    )


@app.post("/v1/agent", response_model=AgentResponse)
async def run_agent_endpoint(request: AgentRequest):
    """运行 Agent 模式"""
    logger.info(f"Received agent request: {request.query} (mode: {request.mode})")
    
    context = {
        "cwd": request.cwd or ".",
        "os": request.os,
        "shell": request.shell,
    }
    
    # 确定模式
    mode_map = {
        "default": AgentMode.DEFAULT,
        "auto": AgentMode.AUTO,
        "full_auto": AgentMode.FULL_AUTO,
    }
    mode = mode_map.get(request.mode, AgentMode.DEFAULT)
    
    # 如果是自动确认模式，使用 full_auto
    if request.auto_confirm:
        mode = AgentMode.FULL_AUTO
    
    steps = []
    
    # 定义确认回调
    async def on_command(cmd: str, result: ActionResult) -> bool:
        if request.auto_confirm:
            return True
        # 在实际使用中，这里应该通过某种方式询问用户
        # 目前先返回 True（自动确认）
        logger.info(f"Command confirmation: {cmd}")
        return True
    
    try:
        agent = Agent(mode=mode, on_command=on_command)
        results = await agent.run(request.query, context)
        
        for i, result in enumerate(results):
            steps.append(AgentStepResponse(
                iteration=i + 1,
                action=result.action,
                command=getattr(result, 'command', None),
                success=result.success,
                output=result.output[:500] if result.output else "",
                error=result.error[:200] if result.error else "",
                needs_confirmation=result.needs_confirmation,
                is_dangerous=result.is_dangerous
            ))
        
        return AgentResponse(
            success=agent.state.task_complete,
            message=agent.state.final_message or "Agent 执行完成",
            steps=steps
        )
        
    except Exception as e:
        logger.error(f"Agent error: {e}")
        return AgentResponse(
            success=False,
            message=f"Agent 执行出错: {str(e)}",
            steps=steps
        )


@app.post("/v1/command/result")
async def report_command_result(request: CommandResultRequest):
    """上报命令执行结果"""
    logger.info(f"Command result: {request.command} (exit: {request.exit_code})")
    
    # 记录到上下文收集器
    collector = get_collector()
    collector.add_command_result(
        command=request.command,
        exit_code=request.exit_code,
        stdout=request.stdout,
        stderr=request.stderr
    )
    
    return {"status": "ok"}


# ============== Agent Session 端点（Stage 2 核心）==============

@app.post("/v1/agent/session/start", response_model=AgentSessionStepResult)
async def agent_session_start(request: AgentSessionStartRequest):
    """启动一个 Agent 会话，返回第一步建议命令。
    
    前端(Shell 插件)：
        1. 用户输入任务描述，双击 Tab → 调用此接口
        2. 返回 session_id + 第一步命令建议
        3. Shell 插件将命令放入缓冲区，等待用户确认
        4. 用户回车执行后，Shell 插件调用 /v1/agent/session/step 上报结果
    """
    logger.info(f"Starting agent session: {request.task!r} (mode: {request.mode})")
    
    mode_map = {
        "default": AgentMode.DEFAULT,
        "auto": AgentMode.AUTO,
        "full_auto": AgentMode.FULL_AUTO,
    }
    mode = mode_map.get(request.mode, AgentMode.DEFAULT)
    
    context = {
        "cwd": request.cwd or ".",
        "os": request.os,
        "shell": request.shell,
    }
    
    config = get_config()
    mgr = get_session_manager()
    session = mgr.create(
        task=request.task,
        context=context,
        mode=mode,
        max_iterations=config.agent.max_iterations,
    )
    
    try:
        session, result = await mgr.advance(session.session_id)
        if result is None:
            return AgentSessionStepResult(
                session_id=session.session_id,
                iteration=session.iteration,
                action="done",
                task_complete=True,
                final_message="任务立即完成",
            )
        
        cmd = result.command if result.action == "execute" else None
        # 对 DEFAULT/AUTO 模式的危险命令设置 needs_confirmation
        needs_conf = False
        if mode == AgentMode.DEFAULT:
            needs_conf = True
        elif mode == AgentMode.AUTO and result.is_dangerous:
            needs_conf = True
        
        return AgentSessionStepResult(
            session_id=session.session_id,
            iteration=session.iteration,
            action=result.action,
            command=cmd,
            output=result.output[:500],
            error=result.error[:200],
            is_dangerous=result.is_dangerous,
            needs_confirmation=needs_conf,
            task_complete=session.task_complete,
            final_message=session.final_message,
        )
    except Exception as e:
        logger.error(f"Agent session start error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/agent/session/step", response_model=AgentSessionStepResult)
async def agent_session_step(request: AgentSessionStepRequest):
    """推进 Agent 会话一步：上报上一步执行结果，获取下一步建议。
    
    调用时机：用户在 Shell 中执行了上一步建议的命令后，
    Shell 插件将执行结果上报此接口，获取下一步行动。
    """
    mgr = get_session_manager()
    session = mgr.get(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {request.session_id!r} 不存在或已过期")
    
    if session.task_complete:
        return AgentSessionStepResult(
            session_id=session.session_id,
            iteration=session.iteration,
            action="done",
            task_complete=True,
            final_message=session.final_message or "任务已完成",
        )
    
    # 构造上一步结果字符串
    user_reply = None
    if request.last_command:
        parts = [f"命令: {request.last_command}", f"退出码: {request.last_exit_code}"]
        if request.last_stdout:
            parts.append(f"stdout:\n{request.last_stdout[:1000]}")
        if request.last_stderr:
            parts.append(f"stderr:\n{request.last_stderr[:500]}")
        user_reply = "\n".join(parts)
    
    try:
        session, result = await mgr.advance(request.session_id, last_command_result=user_reply)
        if result is None:
            return AgentSessionStepResult(
                session_id=session.session_id,
                iteration=session.iteration,
                action="done",
                task_complete=True,
                final_message=session.final_message or "已达到最大迭代次数",
            )
        
        cmd = result.command if result.action == "execute" else None
        needs_conf = False
        if session.mode == AgentMode.DEFAULT:
            needs_conf = True
        elif session.mode == AgentMode.AUTO and result.is_dangerous:
            needs_conf = True
        
        return AgentSessionStepResult(
            session_id=session.session_id,
            iteration=session.iteration,
            action=result.action,
            command=cmd,
            output=result.output[:500],
            error=result.error[:200],
            is_dangerous=result.is_dangerous,
            needs_confirmation=needs_conf,
            task_complete=session.task_complete,
            final_message=session.final_message,
        )
    except Exception as e:
        logger.error(f"Agent session step error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/agent/session/{session_id}", response_model=AgentSessionStatusResponse)
async def agent_session_status(session_id: str):
    """获取 Agent 会话状态"""
    mgr = get_session_manager()
    session = mgr.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} 不存在")
    
    return AgentSessionStatusResponse(
        session_id=session.session_id,
        task=session.task,
        mode=session.mode.value,
        iteration=session.iteration,
        max_iterations=session.max_iterations,
        task_complete=session.task_complete,
        final_message=session.final_message,
        history_length=len(session.history),
    )


@app.delete("/v1/agent/session/{session_id}")
async def agent_session_delete(session_id: str):
    """删除（取消）Agent 会话"""
    mgr = get_session_manager()
    if mgr.get(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} 不存在")
    mgr.delete(session_id)
    return {"status": "ok", "message": f"会话 {session_id} 已删除"}


@app.get("/v1/agent/sessions")
async def agent_sessions_list():
    """列出所有活跃 Agent 会话"""
    mgr = get_session_manager()
    sessions = mgr.list_sessions()
    return {
        "count": len(sessions),
        "sessions": [
            {
                "session_id": s.session_id,
                "task": s.task[:80],
                "mode": s.mode.value,
                "iteration": s.iteration,
                "task_complete": s.task_complete,
                "created_at": s.created_at.isoformat(),
            }
            for s in sessions
        ]
    }




@app.post("/debug/mock-suggest")
async def mock_suggestion(request: SuggestionRequest):
    """模拟建议（不调用 LLM，用于调试）"""
    query = request.query.lower()
    
    # 多步任务关键词 → use_agent=True
    multi_step_keywords = ["然后", "并且", "批量", "部署", "搭建", "and then", "then", "batch"]
    for kw in multi_step_keywords:
        if kw in query:
            return SuggestionResponse(
                command="",
                explanation="此任务建议使用 Agent 模式",
                is_dangerous=False,
                use_agent=True,
            )
    
    mock_commands = {
        "查找大文件": "find . -type f -size +100M",
        "查看端口": "lsof -i :8080",
        "列出文件": "ls -la",
        "当前目录": "pwd",
        "查看进程": "ps aux",
        "磁盘空间": "df -h",
        "内存使用": "free -h",
        "网络连接": "netstat -tunlp",
        "git状态": "git status",
        "git日志": "git log --oneline -10",
    }
    
    for key, cmd in mock_commands.items():
        if key in query:
            return SuggestionResponse(
                command=cmd,
                explanation=f"Mock response for: {request.query}",
                is_dangerous=False,
                use_agent=False,
            )
    
    # 默认返回
    return SuggestionResponse(
        command=f"echo 'Mock: {request.query}'",
        explanation="Mock response",
        is_dangerous=False,
        use_agent=False,
    )


@app.post("/debug/mock-agent")
async def mock_agent(request: AgentRequest):
    """模拟 Agent（不调用 LLM，用于调试）"""
    steps = [
        AgentStepResponse(
            iteration=1,
            action="execute",
            command="ls -la",
            success=True,
            output="total 48\ndrwxr-xr-x  2 user user 4096 ...",
            needs_confirmation=False,
            is_dangerous=False
        ),
        AgentStepResponse(
            iteration=2,
            action="done",
            success=True,
            output="任务完成：已列出当前目录内容",
            needs_confirmation=False,
            is_dangerous=False
        )
    ]
    
    return AgentResponse(
        success=True,
        message="Mock Agent 执行完成",
        steps=steps
    )


# 预设 Agent 脚本（用于调试会话流程，不调用 LLM）
_DEBUG_SCRIPTS: Dict[str, List[Dict[str, Any]]] = {
    "list_files": [
        {"action": "execute", "command": "ls -la"},
        {"action": "execute", "command": "ls -la | wc -l"},
        {"action": "done", "message": "已列出目录文件并统计数量"},
    ],
    "check_system": [
        {"action": "execute", "command": "uname -a"},
        {"action": "execute", "command": "df -h"},
        {"action": "execute", "command": "free -h"},
        {"action": "done", "message": "系统信息检查完成"},
    ],
}

# 调试会话步骤游标（session_id → step_index）
_debug_session_steps: Dict[str, int] = {}


@app.post("/debug/agent-session/start")
async def debug_agent_session_start(
    task: str = "list_files",
    mode: str = "default",
):
    """调试用：使用预设脚本启动 Agent 会话（无需 LLM）。
    参数 task: list_files | check_system
    """
    if task not in _DEBUG_SCRIPTS:
        raise HTTPException(status_code=400, detail=f"未知调试脚本: {task}，可选: {list(_DEBUG_SCRIPTS.keys())}")
    
    mode_map = {"default": AgentMode.DEFAULT, "auto": AgentMode.AUTO, "full_auto": AgentMode.FULL_AUTO}
    agent_mode = mode_map.get(mode, AgentMode.DEFAULT)
    
    mgr = get_session_manager()
    session = mgr.create(
        task=task,
        context={"cwd": ".", "os": "Linux", "shell": "zsh"},
        mode=agent_mode,
    )
    
    _debug_session_steps[session.session_id] = 0
    
    # 返回第一步
    step = _DEBUG_SCRIPTS[task][0]
    _debug_session_steps[session.session_id] = 1
    
    needs_conf = agent_mode == AgentMode.DEFAULT
    return AgentSessionStepResult(
        session_id=session.session_id,
        iteration=1,
        action=step["action"],
        command=step.get("command"),
        output=step.get("message", ""),
        is_dangerous=False,
        needs_confirmation=needs_conf,
        task_complete=step["action"] == "done",
        final_message=step.get("message", "") if step["action"] == "done" else "",
    )


@app.post("/debug/agent-session/step")
async def debug_agent_session_step(request: AgentSessionStepRequest):
    """调试用：推进预设脚本 Agent 会话一步"""
    session_id = request.session_id
    mgr = get_session_manager()
    session = mgr.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} 不存在")
    
    if session_id not in _debug_session_steps:
        raise HTTPException(status_code=400, detail="该会话不是调试会话")
    
    script = _DEBUG_SCRIPTS.get(session.task, [])
    idx = _debug_session_steps.get(session_id, 0)
    
    if idx >= len(script):
        session.task_complete = True
        mgr.update(session)
        return AgentSessionStepResult(
            session_id=session_id,
            iteration=idx + 1,
            action="done",
            task_complete=True,
            final_message="调试脚本已执行完毕",
        )
    
    step = script[idx]
    _debug_session_steps[session_id] = idx + 1
    session.iteration = idx + 1
    
    is_done = step["action"] == "done"
    if is_done:
        session.task_complete = True
        session.final_message = step.get("message", "")
    
    mgr.update(session)
    
    needs_conf = session.mode == AgentMode.DEFAULT and not is_done
    return AgentSessionStepResult(
        session_id=session_id,
        iteration=idx + 1,
        action=step["action"],
        command=step.get("command"),
        output=step.get("message", ""),
        is_dangerous=False,
        needs_confirmation=needs_conf,
        task_complete=is_done,
        final_message=step.get("message", "") if is_done else "",
    )


# ============== 启动函数 ==============

def start_daemon(host: str = None, port: int = None):
    """启动 Daemon 服务"""
    config = get_config()
    host = host or config.daemon.host
    port = port or config.daemon.port
    
    logger.info(f"Starting auto-shell daemon on {host}:{port}")
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=config.daemon.log_level
    )


if __name__ == "__main__":
    start_daemon()
