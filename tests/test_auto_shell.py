"""
auto-shell 测试套件
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
import json

# 测试配置模块
class TestConfig:
    def test_default_config(self):
        """测试默认配置"""
        from auto_shell.config import Config, LLMConfig, DaemonConfig, AgentConfig
        
        config = Config()
        assert config.llm.api_base == "http://127.0.0.1:8000/v1"
        assert config.daemon.port == 28001
        assert config.agent.default_mode == "default"
    
    def test_config_from_env(self, monkeypatch):
        """测试环境变量覆盖"""
        monkeypatch.setenv("AUTO_SHELL_API_BASE", "http://custom.api/v1")
        monkeypatch.setenv("AUTO_SHELL_MODEL", "custom-model")
        
        from auto_shell.config import load_config
        config = load_config()
        
        assert config.llm.api_base == "http://custom.api/v1"
        assert config.llm.model == "custom-model"


# 测试上下文收集器
class TestContextCollector:
    def test_collect_context(self):
        """测试上下文收集"""
        from auto_shell.context import ContextCollector
        
        collector = ContextCollector()
        context = collector.collect("test query", "bash")
        
        assert context.user_query == "test query"
        assert context.shell == "bash"
        assert context.os != ""
        assert context.cwd != ""
    
    def test_command_history(self):
        """测试命令历史记录"""
        from auto_shell.context import ContextCollector
        
        collector = ContextCollector()
        collector.add_command_result("ls -la", 0, "file1\nfile2", "")
        
        context = collector.collect("", "bash")
        assert context.last_command is not None
        assert context.last_command.command == "ls -la"
        assert context.last_command.exit_code == 0


# 测试 LLM 客户端
class TestLLMClient:
    def test_clean_command(self):
        """测试命令清理"""
        from auto_shell.llm_client import LLMClient
        
        client = LLMClient()
        
        # 测试 markdown 代码块清理
        assert client._clean_command("```bash\nls -la\n```") == "ls -la"
        
        # 测试控制字符清理
        assert "\x00" not in client._clean_command("ls\x00-la")
        
        # 测试单行化
        assert "\n" not in client._clean_command("ls\n-la")
    
    def test_format_context(self):
        """测试上下文格式化"""
        from auto_shell.llm_client import LLMClient
        
        client = LLMClient()
        context = {
            "os": "Linux",
            "shell": "bash",
            "cwd": "/home/user",
        }
        
        result = client._format_context(context)
        assert "Linux" in result
        assert "bash" in result
        assert "/home/user" in result


# 测试 Agent
class TestAgent:
    def test_is_safe_command(self):
        """测试命令安全检查"""
        from auto_shell.agent import Agent, AgentMode
        from auto_shell.config import Config
        
        # 使用模拟配置
        with patch('auto_shell.agent.get_config') as mock_config:
            config = Config()
            mock_config.return_value = config
            
            agent = Agent()
            
            # 安全命令
            assert agent.is_safe_command("ls -la") == True
            assert agent.is_safe_command("cat file.txt") == True
            
            # 危险命令
            assert agent.is_safe_command("rm -rf /") == False
            assert agent.is_safe_command("sudo apt install") == False
    
    def test_needs_confirmation(self):
        """测试确认需求判断"""
        from auto_shell.agent import Agent, AgentMode
        from auto_shell.config import Config
        
        with patch('auto_shell.agent.get_config') as mock_config:
            config = Config()
            mock_config.return_value = config
            
            # DEFAULT 模式：所有命令都需要确认
            agent = Agent(mode=AgentMode.DEFAULT)
            assert agent.needs_confirmation("ls -la") == True
            
            # AUTO 模式：只有危险命令需要确认
            agent = Agent(mode=AgentMode.AUTO)
            assert agent.needs_confirmation("ls -la") == False
            assert agent.needs_confirmation("rm file") == True
            
            # FULL_AUTO 模式：不需要确认
            agent = Agent(mode=AgentMode.FULL_AUTO)
            assert agent.needs_confirmation("rm -rf /") == False
    
    @pytest.mark.asyncio
    async def test_execute_command(self):
        """测试命令执行"""
        from auto_shell.agent import Agent, AgentMode
        from auto_shell.config import Config
        
        with patch('auto_shell.agent.get_config') as mock_config:
            config = Config()
            mock_config.return_value = config
            
            agent = Agent(mode=AgentMode.FULL_AUTO)
            result = await agent.execute_command("echo 'hello'")
            
            assert result.success == True
            assert "hello" in result.output


# 测试 API 端点
class TestAPI:
    @pytest.fixture
    def client(self):
        """创建测试客户端"""
        from fastapi.testclient import TestClient
        from auto_shell.server import app
        return TestClient(app)
    
    def test_health_check(self, client):
        """测试健康检查"""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    
    def test_mock_suggest(self, client):
        """测试模拟建议"""
        response = client.post(
            "/debug/mock-suggest",
            json={
                "query": "查找大文件",
                "cwd": "/home/user",
                "os": "Linux",
                "shell": "bash"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "find" in data["command"]
    
    def test_mock_agent(self, client):
        """测试模拟 Agent"""
        response = client.post(
            "/debug/mock-agent",
            json={
                "query": "列出当前目录",
                "cwd": "/home/user",
                "os": "Linux",
                "shell": "bash",
                "mode": "default"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] == True
        assert len(data["steps"]) > 0


# 运行测试
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
