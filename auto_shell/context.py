"""
上下文收集器模块
收集终端环境信息作为 LLM 的上下文
"""

import os
import platform
import subprocess
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

logger = logging.getLogger("auto-shell-context")


class CommandHistory(BaseModel):
    """命令历史记录"""
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)


class Context(BaseModel):
    """终端上下文"""
    # 基础环境信息
    os: str = ""
    os_version: str = ""
    shell: str = ""
    cwd: str = ""
    user: str = ""
    hostname: str = ""
    
    # 命令历史
    last_command: Optional[CommandHistory] = None
    command_history: List[CommandHistory] = Field(default_factory=list)
    
    # 用户请求
    user_query: str = ""
    
    # 模型思考过程 (用于 Agent 模式)
    model_thoughts: List[str] = Field(default_factory=list)
    
    # 屏幕缓冲区 (可选)
    screen_buffer: str = ""
    
    # 环境变量 (可选，敏感信息需过滤)
    env_vars: Dict[str, str] = Field(default_factory=dict)


class ContextCollector:
    """上下文收集器"""
    
    def __init__(self, max_history: int = 10):
        self.max_history = max_history
        self._history: List[CommandHistory] = []
    
    def collect(self, query: str = "", shell_type: str = "zsh") -> Context:
        """收集当前终端上下文"""
        context = Context(
            os=platform.system(),
            os_version=platform.release(),
            shell=shell_type,
            cwd=os.getcwd(),
            user=os.environ.get("USER", "unknown"),
            hostname=platform.node(),
            user_query=query,
        )
        
        # 收集一些有用的环境变量
        safe_env_keys = ["PATH", "HOME", "LANG", "TERM", "SHELL", "EDITOR", "PWD"]
        context.env_vars = {
            k: os.environ.get(k, "") 
            for k in safe_env_keys 
            if k in os.environ
        }
        
        # 添加命令历史
        context.command_history = self._history[-self.max_history:]
        if self._history:
            context.last_command = self._history[-1]
        
        return context
    
    def add_command_result(
        self, 
        command: str, 
        exit_code: int, 
        stdout: str = "", 
        stderr: str = ""
    ):
        """添加命令执行结果到历史"""
        entry = CommandHistory(
            command=command,
            exit_code=exit_code,
            stdout=stdout[:1000],  # 限制长度
            stderr=stderr[:1000],
        )
        self._history.append(entry)
        
        # 保持历史记录数量限制
        if len(self._history) > self.max_history * 2:
            self._history = self._history[-self.max_history:]
    
    def add_model_thought(self, thought: str):
        """添加模型思考过程"""
        pass  # 由 Agent 模式使用
    
    def get_context_summary(self, context: Context) -> str:
        """生成上下文摘要，用于 LLM prompt"""
        lines = [
            f"操作系统: {context.os} {context.os_version}",
            f"Shell: {context.shell}",
            f"当前目录: {context.cwd}",
            f"用户: {context.user}@{context.hostname}",
        ]
        
        if context.last_command:
            lines.append(f"上一条命令: {context.last_command.command}")
            lines.append(f"退出码: {context.last_command.exit_code}")
            if context.last_command.stdout:
                lines.append(f"标准输出: {context.last_command.stdout[:200]}")
            if context.last_command.stderr:
                lines.append(f"标准错误: {context.last_command.stderr[:200]}")
        
        return "\n".join(lines)


# 全局上下文收集器实例
_collector: Optional[ContextCollector] = None


def get_collector() -> ContextCollector:
    """获取全局上下文收集器实例"""
    global _collector
    if _collector is None:
        _collector = ContextCollector()
    return _collector
