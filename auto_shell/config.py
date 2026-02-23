"""
配置管理模块
支持从 config.yaml 或环境变量加载配置
"""

import os
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, Field
import yaml
import logging

logger = logging.getLogger("auto-shell-config")


class LLMConfig(BaseModel):
    """LLM API 配置"""
    api_base: str = "http://127.0.0.1:8000/v1"
    api_key: str = "sk-dummy-key"
    model: str = "gpt-3.5-turbo"
    temperature: float = 0.1
    max_tokens: int = 200


class DaemonConfig(BaseModel):
    """Daemon 服务配置"""
    host: str = "127.0.0.1"
    port: int = 28001
    log_level: str = "info"


class AgentConfig(BaseModel):
    """Agent 模式配置"""
    default_mode: str = "default"  # default, auto, full_auto
    max_iterations: int = 10
    safe_commands: List[str] = Field(default_factory=lambda: [
        "^ls", "^cat", "^echo", "^pwd", "^which", "^grep", "^find", "^head", "^tail", "^wc"
    ])
    dangerous_commands: List[str] = Field(default_factory=lambda: [
        "^rm", "^sudo", "^chmod", "^chown", "^mkfs", "^dd", "^>", "^>>"
    ])


class ShellConfig(BaseModel):
    """Shell 插件配置"""
    double_tab_threshold: int = 400  # 毫秒
    request_timeout: int = 30  # 秒


class Config(BaseModel):
    """主配置类"""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    shell: ShellConfig = Field(default_factory=ShellConfig)


def find_config_file() -> Optional[Path]:
    """查找配置文件，按优先级搜索"""
    # 优先级顺序：
    # 1. 环境变量指定的路径
    # 2. 当前目录
    # 3. 用户主目录
    # 4. /etc/auto-shell/
    
    search_paths = []
    
    # 环境变量
    env_path = os.environ.get("AUTO_SHELL_CONFIG")
    if env_path:
        search_paths.append(Path(env_path))
    
    # 当前目录
    search_paths.extend([
        Path.cwd() / "config.yaml",
        Path.cwd() / ".auto-shell" / "config.yaml",
    ])
    
    # 用户主目录
    home = Path.home()
    search_paths.extend([
        home / ".auto-shell" / "config.yaml",
        home / ".config" / "auto-shell" / "config.yaml",
    ])
    
    # 系统目录
    search_paths.append(Path("/etc/auto-shell/config.yaml"))
    
    for path in search_paths:
        if path.exists():
            logger.debug(f"Found config file: {path}")
            return path
    
    return None


def load_config(config_path: Optional[Path] = None) -> Config:
    """加载配置文件"""
    if config_path is None:
        config_path = find_config_file()
    
    if config_path and config_path.exists():
        logger.info(f"Loading config from: {config_path}")
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        config = Config(**data)
    else:
        logger.info("No config file found, using defaults")
        config = Config()
    
    # 环境变量覆盖
    config.llm.api_base = os.environ.get("AUTO_SHELL_API_BASE", config.llm.api_base)
    config.llm.api_key = os.environ.get("AUTO_SHELL_API_KEY", config.llm.api_key)
    config.llm.model = os.environ.get("AUTO_SHELL_MODEL", config.llm.model)
    config.daemon.host = os.environ.get("AUTO_SHELL_DAEMON_HOST", config.daemon.host)
    config.daemon.port = int(os.environ.get("AUTO_SHELL_DAEMON_PORT", config.daemon.port))
    
    return config


# 全局配置实例
_config: Optional[Config] = None


def get_config() -> Config:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> Config:
    """重新加载配置"""
    global _config
    _config = load_config()
    return _config
