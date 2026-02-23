import os
import logging
from openai import AsyncOpenAI

logger = logging.getLogger("auto-shell-llm")

# 尝试从环境变量获取配置，如果没有则使用默认值
# 默认使用本地的 iflow2api 或其他兼容 OpenAI 的服务
API_BASE_URL = os.environ.get("AUTO_SHELL_API_BASE", "http://127.0.0.1:8000/v1")
API_KEY = os.environ.get("AUTO_SHELL_API_KEY", "sk-dummy-key")
MODEL_NAME = os.environ.get("AUTO_SHELL_MODEL", "gpt-3.5-turbo")

# 初始化异步 OpenAI 客户端
client = AsyncOpenAI(
    base_url=API_BASE_URL,
    api_key=API_KEY,
)

SYSTEM_PROMPT = """你是一个 Linux/Unix 终端命令专家。
用户会输入自然语言描述，你需要生成对应的 shell 命令。

当前环境：
- 操作系统: {os}
- Shell: {shell}
- 当前目录: {cwd}

规则：
1. 绝对禁止输出任何思考过程、解释、前缀或后缀。
2. 绝对禁止使用 Markdown 代码块 (如 ```bash)。
3. 你的输出必须且只能是可以在终端直接执行的单行命令。
4. 使用最常见、最安全的命令组合。
5. 优先使用 GNU 核心工具。

示例：
用户: 查找大文件
返回: find . -type f -size +100M

用户: 查看端口占用
返回: lsof -i :8080
"""

async def generate_command(query: str, context: dict) -> str:
    """
    调用兼容 OpenAI 的 API 生成命令
    """
    prompt = SYSTEM_PROMPT.format(**context)
    
    try:
        logger.info(f"Calling LLM API ({MODEL_NAME}) at {API_BASE_URL}...")
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": query}
            ],
            temperature=0.1, # 保持较低的温度以获得确定的命令
            max_tokens=100,
        )
        
        command = response.choices[0].message.content.strip()
        
        # 简单的后处理：移除可能存在的 markdown 代码块标记
        if command.startswith("```"):
            lines = command.split("\n")
            if len(lines) >= 2:
                command = "\n".join(lines[1:]).replace("```", "").strip()
                
        # 修复：如果 LLM 还是输出了多行（比如包含了思考过程），我们只取最后一行看起来像命令的
        # 或者更简单粗暴：只取第一行非空行，因为我们要求它只输出命令
        lines = [line.strip() for line in command.split("\n") if line.strip()]
        if lines:
            # 尝试找到第一行不包含中文的行（假设命令通常不包含中文）
            # 如果都包含，就取最后一行
            command = lines[-1]
            for line in lines:
                if not any('\u4e00' <= char <= '\u9fff' for char in line):
                    command = line
                    break
        else:
            command = ""
                
        # 修复：清理 LLM 返回的控制字符 (如 \x00-\x1f)，防止破坏 JSON 结构
        # 保留换行符 (\n) 和制表符 (\t)
        import re
        command = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', command)
        
        # 最终确保命令是单行，避免 JSON 序列化问题
        command = command.replace("\n", " ").replace("\r", "")
                
        return command
        
    except Exception as e:
        logger.error(f"LLM API call failed: {e}")
        # 发生错误时返回空字符串，Zsh 插件会处理
        return ""
