"""
LLM 客户端模块
与 OpenAI 兼容的 API 通信
"""

import re
import logging
from typing import Optional, List, Dict, Any, AsyncGenerator
from openai import AsyncOpenAI
from pydantic import BaseModel

from .config import get_config

logger = logging.getLogger("auto-shell-llm")


# 命令生成 System Prompt（工具调用模式下无需格式约束）
COMMAND_SYSTEM_PROMPT = """你是一个 Linux/Unix 终端命令专家。
用户会输入自然语言描述，请调用 run_shell_command 工具，传入对应的 shell 命令。

当前环境：
{context}

要求：
- 命令必须可以在终端直接执行
- 优先使用 GNU 核心工具
- 高危命令（rm -rf、sudo 等）需谨慎
"""

# Agent 模式 System Prompt
AGENT_SYSTEM_PROMPT = """你是一个智能终端助手，帮助用户完成复杂的多步骤任务。

当前环境：
{context}

工作流程：分析需求 → 逐步执行 → 根据结果调整计划。
每次只调用一个工具，等待结果后继续。高危命令需先确认。
"""

# 命令生成工具 schema
_TOOL_RUN_COMMAND = {
    "type": "function",
    "function": {
        "name": "run_shell_command",
        "description": "在终端执行一条 shell 命令",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令，必须可直接在终端运行"
                }
            },
            "required": ["command"]
        }
    }
}

# Agent 工具 schema
_AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "执行一条 shell 命令并获取输出",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "向文件写入内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "写入内容"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "向用户提问或请求确认",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "问题内容"}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_done",
            "description": "任务已完成，报告结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "完成说明"}
                },
                "required": ["message"]
            }
        }
    }
]


class LLMClient:
    """LLM 客户端"""
    
    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None
        self._config = None
    
    def _get_client(self) -> AsyncOpenAI:
        """获取或创建 OpenAI 客户端（配置变更时自动重建）"""
        config = get_config()
        if (
            self._client is None
            or getattr(self, '_cached_api_base', None) != config.llm.api_base
            or getattr(self, '_cached_api_key', None) != config.llm.api_key
        ):
            self._client = AsyncOpenAI(
                base_url=config.llm.api_base,
                api_key=config.llm.api_key,
            )
            self._cached_api_base = config.llm.api_base
            self._cached_api_key = config.llm.api_key
        return self._client
    
    async def generate_command(
        self, 
        query: str, 
        context: Dict[str, Any]
    ) -> str:
        """生成单条命令（工具调用模式，回退文本解析）"""
        import json as _json
        client = self._get_client()
        config = get_config()

        context_str = self._format_context(context)
        prompt = COMMAND_SYSTEM_PROMPT.format(context=context_str)

        # 工具调用需要足够的 tokens 让模型完成推理链 + 输出工具调用
        # 不使用 config.max_tokens（用户可能设为很小），强制 1024
        TOOL_CALL_MAX_TOKENS = 1024

        try:
            logger.info(f"Calling LLM API ({config.llm.model}) with tool_call...")
            response = await client.chat.completions.create(
                model=config.llm.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": query}
                ],
                temperature=config.llm.temperature,
                max_tokens=TOOL_CALL_MAX_TOKENS,
                tools=[_TOOL_RUN_COMMAND],
                tool_choice={"type": "function", "function": {"name": "run_shell_command"}},
            )

            msg = response.choices[0].message

            # 提取第一个工具调用的 command 参数
            if msg.tool_calls:
                raw_args = msg.tool_calls[0].function.arguments or ""
                if raw_args.strip():
                    try:
                        args = _json.loads(raw_args)
                        command = args.get("command", "").strip()
                        if command:
                            logger.info(f"Tool call command: {command}")
                            return command
                    except _json.JSONDecodeError:
                        logger.warning(f"tool_calls arguments JSON parse failed: {raw_args[:100]!r}")

            # 工具调用未生效，回退到文本解析
            logger.warning("No valid tool_calls, falling back to text parsing")
            command = (msg.content or "").strip()
            command = self._clean_command(command)
            logger.info(f"Fallback command: {command!r}")
            return command

        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
            return ""
    
    async def generate_agent_action(
        self,
        query: str,
        context: Dict[str, Any],
        history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """生成 Agent 动作（工具调用）"""
        import json as _json
        client = self._get_client()
        config = get_config()

        context_str = self._format_context(context)
        prompt = AGENT_SYSTEM_PROMPT.format(context=context_str)

        messages = [{"role": "system", "content": prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": query})

        try:
            response = await client.chat.completions.create(
                model=config.llm.model,
                messages=messages,
                temperature=config.llm.temperature,
                max_tokens=500,
                tools=_AGENT_TOOLS,
                tool_choice="required",
            )

            msg = response.choices[0].message

            # 解析工具调用
            if msg.tool_calls:
                tc = msg.tool_calls[0]
                func_name = tc.function.name
                args = _json.loads(tc.function.arguments)

                # 统一映射到内部 action 格式
                action_map = {
                    "execute_command": lambda a: {"action": "execute", "command": a.get("command", "")},
                    "read_file":       lambda a: {"action": "read_file", "path": a.get("path", "")},
                    "write_file":      lambda a: {"action": "write_file", "path": a.get("path", ""), "content": a.get("content", "")},
                    "ask_user":        lambda a: {"action": "ask_user", "question": a.get("question", "")},
                    "task_done":       lambda a: {"action": "done", "message": a.get("message", "")},
                }
                if func_name in action_map:
                    return action_map[func_name](args)
                return {"action": func_name, **args}

            # 没有工具调用，回退到文本解析
            content = (msg.content or "").strip()
            return self._parse_agent_action(content)

        except Exception as e:
            logger.error(f"Agent action generation failed: {e}")
            return {"action": "error", "message": str(e)}
    
    async def stream_command(
        self,
        query: str,
        context: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        """流式生成命令 (用于实时显示)"""
        client = self._get_client()
        config = get_config()
        
        context_str = self._format_context(context)
        prompt = COMMAND_SYSTEM_PROMPT.format(context=context_str)
        
        try:
            stream = await client.chat.completions.create(
                model=config.llm.model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": query}
                ],
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens,
                stream=True,
            )
            
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
                    
        except Exception as e:
            logger.error(f"Stream generation failed: {e}")
            yield f"echo 'Error: {e}'"
    
    def _format_context(self, context: Dict[str, Any]) -> str:
        """格式化上下文为字符串"""
        lines = []
        if "os" in context:
            lines.append(f"- 操作系统: {context['os']}")
        if "shell" in context:
            lines.append(f"- Shell: {context['shell']}")
        if "cwd" in context:
            lines.append(f"- 当前目录: {context['cwd']}")
        if "last_command" in context:
            lines.append(f"- 上一条命令: {context['last_command']}")
        if "last_exit_code" in context:
            lines.append(f"- 上次退出码: {context['last_exit_code']}")
        
        return "\n".join(lines) if lines else "未知环境"
    
    def _clean_command(self, command: str) -> str:
        """清理 LLM 返回的命令"""
        # 1. 优先提取 COMMAND: 标记后的内容（新 prompt 格式）
        found_marker = False
        for line in command.split("\n"):
            stripped = line.strip()
            if stripped.upper().startswith("COMMAND:"):
                command = stripped[len("COMMAND:"):].strip().strip('`\'"')
                found_marker = True
                break

        if not found_marker:
            # 2. 移除 markdown 代码块
            if command.startswith("```"):
                lines = command.split("\n")
                if len(lines) >= 2:
                    command = "\n".join(lines[1:]).replace("```", "").strip()

            # 3. 多行：优先取不含中文的行
            lines = [l.strip() for l in command.split("\n") if l.strip()]
            if lines:
                command = lines[-1]
                for line in lines:
                    if not any('\u4e00' <= char <= '\u9fff' for char in line):
                        command = line
                        break
            else:
                command = ""

        # 4. 移除控制字符
        command = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', command)
        # 5. 确保单行
        command = command.replace("\n", " ").replace("\r", "")
        # 6. 校验：若结果几乎全是中文字符，视为无效命令
        if command:
            chinese_count = sum(1 for c in command if '\u4e00' <= c <= '\u9fff')
            if len(command) > 0 and chinese_count / len(command) > 0.4:
                logger.warning(f"LLM returned non-command text: {command[:80]!r}")
                return ""
        return command
    
    def _parse_agent_action(self, content: str) -> Dict[str, Any]:
        """解析 Agent 动作"""
        import json
        
        # 尝试提取 JSON
        json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # 解析失败，返回原始内容
        return {"action": "unknown", "raw": content}


# 全局客户端实例
_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取全局 LLM 客户端实例"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
