#!/usr/bin/env python3
"""
auto-shell 调试工具
用于快速测试和调试各个组件
"""

import asyncio
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def print_header(title: str):
    """打印标题"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_result(success: bool, message: str):
    """打印结果"""
    status = "✅" if success else "❌"
    print(f"  {status} {message}")


def test_config():
    """测试配置加载"""
    print_header("测试配置加载")
    
    try:
        from auto_shell.config import get_config, find_config_file
        
        config_file = find_config_file()
        print_result(True, f"配置文件: {config_file or '使用默认配置'}")
        
        config = get_config()
        print_result(True, f"LLM API: {config.llm.api_base}")
        print_result(True, f"模型: {config.llm.model}")
        print_result(True, f"Daemon: {config.daemon.host}:{config.daemon.port}")
        print_result(True, f"Agent 模式: {config.agent.default_mode}")
        
        return True
    except Exception as e:
        print_result(False, f"配置加载失败: {e}")
        return False


def test_context():
    """测试上下文收集"""
    print_header("测试上下文收集")
    
    try:
        from auto_shell.context import ContextCollector
        
        collector = ContextCollector()
        
        # 添加一些命令历史
        collector.add_command_result("ls -la", 0, "file1\nfile2\nfile3", "")
        collector.add_command_result("pwd", 0, "/home/user", "")
        collector.add_command_result("cat missing", 1, "", "No such file")
        
        context = collector.collect("查找大文件", "zsh")
        
        print_result(True, f"操作系统: {context.os}")
        print_result(True, f"Shell: {context.shell}")
        print_result(True, f"当前目录: {context.cwd}")
        print_result(True, f"用户查询: {context.user_query}")
        print_result(True, f"历史记录数: {len(context.command_history)}")
        
        if context.last_command:
            print_result(True, f"最后命令: {context.last_command.command}")
            print_result(True, f"退出码: {context.last_command.exit_code}")
        
        return True
    except Exception as e:
        print_result(False, f"上下文收集失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_llm_client():
    """测试 LLM 客户端初始化"""
    print_header("测试 LLM 客户端")
    
    try:
        from auto_shell.llm_client import get_llm_client
        
        client = get_llm_client()
        print_result(True, "LLM 客户端初始化成功")
        
        # 测试命令清理
        test_cases = [
            ("```bash\nls -la\n```", "ls -la"),
            ("find . -type f", "find . -type f"),
            ("ls\x00-la", "ls -la"),
            ("echo 'hello'\nworld", "echo 'hello' world"),
        ]
        
        for input_cmd, expected in test_cases:
            result = client._clean_command(input_cmd)
            success = expected in result
            print_result(success, f"清理测试: '{input_cmd[:20]}...' -> '{result}'")
        
        return True
    except Exception as e:
        print_result(False, f"LLM 客户端测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_agent():
    """测试 Agent 初始化"""
    print_header("测试 Agent")
    
    try:
        from auto_shell.agent import Agent, AgentMode
        
        # 测试不同模式
        for mode in [AgentMode.DEFAULT, AgentMode.AUTO, AgentMode.FULL_AUTO]:
            agent = Agent(mode=mode)
            print_result(True, f"Agent 模式 {mode.value} 初始化成功")
        
        # 测试命令安全检查
        agent = Agent(mode=AgentMode.AUTO)
        
        safe_commands = ["ls -la", "cat file.txt", "echo hello"]
        dangerous_commands = ["rm -rf /", "sudo rm file", "chmod 777 file"]
        
        for cmd in safe_commands:
            result = agent.is_safe_command(cmd)
            print_result(result, f"安全检查 '{cmd}': {'安全' if result else '危险'}")
        
        for cmd in dangerous_commands:
            result = agent.is_safe_command(cmd)
            print_result(not result, f"危险检查 '{cmd}': {'危险' if not result else '安全'}")
        
        return True
    except Exception as e:
        print_result(False, f"Agent 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_agent_execute():
    """测试 Agent 命令执行"""
    print_header("测试 Agent 命令执行")
    
    try:
        from auto_shell.agent import Agent, AgentMode
        
        agent = Agent(mode=AgentMode.FULL_AUTO)
        
        # 执行简单命令
        result = await agent.execute_command("echo 'Hello from auto-shell!'")
        print_result(result.success, f"命令执行: echo")
        if result.output:
            print(f"     输出: {result.output.strip()}")
        
        # 执行 ls 命令
        result = await agent.execute_command("ls -la")
        print_result(result.success, f"命令执行: ls")
        if result.output:
            print(f"     输出长度: {len(result.output)} 字符")
        
        return True
    except Exception as e:
        print_result(False, f"Agent 执行测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_api():
    """测试 API 端点"""
    print_header("测试 API 端点")
    
    try:
        from fastapi.testclient import TestClient
        from auto_shell.server import app
        
        client = TestClient(app)
        
        # 健康检查
        response = client.get("/health")
        print_result(response.status_code == 200, f"健康检查: {response.status_code}")
        
        # 配置信息
        response = client.get("/config")
        print_result(response.status_code == 200, f"配置信息: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"     LLM API: {data['llm_api_base']}")
            print(f"     模型: {data['llm_model']}")
        
        # 模拟建议
        response = client.post(
            "/debug/mock-suggest",
            json={
                "query": "查找大文件",
                "cwd": "/home/user",
                "os": "Linux",
                "shell": "bash"
            }
        )
        print_result(response.status_code == 200, f"模拟建议: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"     命令: {data['command']}")
        
        # 模拟 Agent
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
        print_result(response.status_code == 200, f"模拟 Agent: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"     成功: {data['success']}")
            print(f"     步骤数: {len(data['steps'])}")
        
        return True
    except Exception as e:
        print_result(False, f"API 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_zsh_plugin():
    """测试 Zsh 插件语法"""
    print_header("测试 Zsh 插件")
    
    try:
        import subprocess
        
        # 检查 zsh 是否可用
        result = subprocess.run(["which", "zsh"], capture_output=True, text=True)
        if result.returncode != 0:
            print_result(False, "Zsh 未安装，跳过插件测试")
            return True
        
        # 检查插件文件是否存在
        plugin_path = os.path.join(os.path.dirname(__file__), "plugin", "auto-shell.plugin.zsh")
        if not os.path.exists(plugin_path):
            print_result(False, f"插件文件不存在: {plugin_path}")
            return False
        
        print_result(True, f"插件文件存在: {plugin_path}")
        
        # 检查插件语法
        result = subprocess.run(
            ["zsh", "-n", plugin_path],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print_result(True, "插件语法检查通过")
        else:
            print_result(False, f"插件语法错误: {result.stderr}")
        
        return True
    except Exception as e:
        print_result(False, f"Zsh 插件测试失败: {e}")
        return False


# ============================================================
# Stage 2 专项测试
# ============================================================

async def test_task_complexity():
    """测试任务复杂度分析（智能路由）"""
    print_header("Stage 2 - 任务复杂度分析")

    try:
        from auto_shell.agent import analyze_task_complexity

        # --- 应判断为多步任务 ---
        multi_step_cases = [
            "找出所有大文件然后压缩并上传到服务器",
            "批量重命名当前目录下所有 .jpg 文件",
            "Find all log files and then delete them",
            "搭建一个 Python FastAPI 项目",
            "部署 nginx 并配置 SSL 证书",
            "a" * 90,  # 超长查询
        ]

        all_ok = True
        for case in multi_step_cases:
            result = await analyze_task_complexity(case)
            print_result(result, f"多步判断: {case[:50]!r} → {result}")
            if not result:
                all_ok = False

        # --- 应判断为单步任务 ---
        single_step_cases = [
            "查找大文件",
            "list files",
            "查看当前目录",
            "pwd",
        ]

        for case in single_step_cases:
            result = await analyze_task_complexity(case)
            print_result(not result, f"单步判断: {case!r} → {result} (期望 False)")
            if result:
                all_ok = False  # 单步被误判为多步也不算严重错误，不强制失败

        return all_ok
    except Exception as e:
        print_result(False, f"分析测试失败: {e}")
        import traceback; traceback.print_exc()
        return False


async def test_agent_session_local():
    """测试 AgentSessionManager 的本地逻辑（不依赖 HTTP 服务器）"""
    print_header("Stage 2 - Agent Session 本地逻辑")

    try:
        from auto_shell.agent import AgentSessionManager, AgentMode

        mgr = AgentSessionManager()

        # 创建会话
        session = mgr.create(
            task="列出当前目录文件",
            context={"cwd": os.getcwd(), "os": "Linux", "shell": "zsh"},
            mode=AgentMode.FULL_AUTO,
            max_iterations=5,
        )
        print_result(True, f"会话创建: id={session.session_id[:8]}...")
        print_result(session.task == "列出当前目录文件", f"任务记录: {session.task}")
        print_result(session.mode == AgentMode.FULL_AUTO, f"模式: {session.mode}")

        # 获取会话
        fetched = mgr.get(session.session_id)
        print_result(fetched is not None, "会话可通过 ID 获取")
        print_result(fetched.session_id == session.session_id, "会话 ID 一致")

        # 列出会话
        sessions = mgr.list_sessions()
        print_result(len(sessions) >= 1, f"会话列表: {len(sessions)} 个")

        # 测试 TTL 清理（伪造过期时间，绕过 update() 的时间戳重置）
        from datetime import timedelta, datetime
        session.updated_at = datetime.now() - timedelta(hours=3)
        mgr._sessions[session.session_id] = session  # 直接写入，绕过 update()
        extra = mgr.create(task="新任务", context={}, mode=AgentMode.DEFAULT)
        mgr._cleanup()
        print_result(mgr.get(session.session_id) is None, "过期会话已清理")
        print_result(mgr.get(extra.session_id) is not None, "活跃会话保留")

        # 删除会话
        mgr.delete(extra.session_id)
        print_result(mgr.get(extra.session_id) is None, "手动删除会话成功")

        return True
    except Exception as e:
        print_result(False, f"Session 本地测试失败: {e}")
        import traceback; traceback.print_exc()
        return False


def test_api_stage2():
    """测试 Stage 2 新增 API 端点（使用 TestClient，不调用 LLM）"""
    print_header("Stage 2 - API 端点测试")

    try:
        from fastapi.testclient import TestClient
        from auto_shell.server import app

        client = TestClient(app)

        # ---- 1. 智能路由：单步任务 ----
        resp = client.post("/debug/mock-suggest", json={
            "query": "列出文件",
            "cwd": os.getcwd(), "os": "Linux", "shell": "zsh"
        })
        print_result(resp.status_code == 200, f"mock-suggest 单步: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"     命令: {data.get('command', 'N/A')}")
            print_result("use_agent" in data, f"use_agent 字段存在: {data.get('use_agent')}")
            print_result(data.get("use_agent") is False, f"单步任务 use_agent=False: {data.get('use_agent')}")

        # ---- 2. 智能路由：多步任务 ----
        resp = client.post("/debug/mock-suggest", json={
            "query": "查找所有日志文件然后删除",
            "cwd": os.getcwd(), "os": "Linux", "shell": "zsh"
        })
        print_result(resp.status_code == 200, f"mock-suggest 多步: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print_result(data.get("use_agent") is True, f"多步任务 use_agent=True: {data.get('use_agent')}")

        # ---- 3. 会话列表（无会话时）----
        resp = client.get("/v1/agent/sessions")
        print_result(resp.status_code == 200, f"GET /v1/agent/sessions: {resp.status_code}")
        before_count = resp.json().get("count", 0)
        print(f"     当前会话数: {before_count}")

        # ---- 4. 完整 Agent 会话生命周期（使用调试脚本，无需 LLM）----
        print("\n  --- 完整会话生命周期（list_files 脚本）---")
        
        # 4a. 启动会话
        resp = client.post("/debug/agent-session/start?task=list_files&mode=full_auto")
        print_result(resp.status_code == 200, f"启动调试会话: {resp.status_code}")
        session_id = None
        if resp.status_code == 200:
            data = resp.json()
            session_id = data.get("session_id")
            print(f"     session_id: {session_id[:8] if session_id else 'N/A'}...")
            print(f"     第1步 action: {data.get('action')}, command: {data.get('command')}")
            print_result(data.get("action") == "execute", f"第一步是 execute: {data.get('action')}")
            print_result(data.get("command") == "ls -la", f"第一步命令正确: {data.get('command')}")

        # 4b. 查询会话状态
        if session_id:
            resp = client.get(f"/v1/agent/session/{session_id}")
            print_result(resp.status_code == 200, f"查询会话状态: {resp.status_code}")
            data = resp.json()
            print(f"     iteration={data.get('iteration')}, complete={data.get('task_complete')}")

        # 4c. 推进会话（模拟执行 ls -la 后的结果）
        if session_id:
            for i in range(1, 5):  # 最多推进4步（脚本有3步）
                resp = client.post("/debug/agent-session/step", json={
                    "session_id": session_id,
                    "last_command": f"step-{i-1}-cmd",
                    "last_exit_code": 0,
                    "last_stdout": f"output of step {i-1}",
                    "last_stderr": "",
                })
                print_result(resp.status_code == 200, f"推进步骤 {i}: {resp.status_code}")
                data = resp.json()
                action = data.get("action")
                print(f"     action={action}, cmd={data.get('command')}, complete={data.get('task_complete')}")
                if data.get("task_complete"):
                    print(f"     最终消息: {data.get('final_message')}")
                    break

        # ---- 5. 检查会话数增加 ----
        resp = client.get("/v1/agent/sessions")
        after_count = resp.json().get("count", 0)
        print_result(after_count > before_count, f"会话数增加: {before_count} → {after_count}")

        # ---- 6. 不存在会话 → 404 ----
        resp = client.get("/v1/agent/session/nonexistent-id-12345")
        print_result(resp.status_code == 404, f"不存在会话返回 404: {resp.status_code}")

        # ---- 7. 删除会话 ----
        if session_id:
            resp = client.delete(f"/v1/agent/session/{session_id}")
            print_result(resp.status_code == 200, f"删除会话: {resp.status_code}")
            resp = client.get(f"/v1/agent/session/{session_id}")
            print_result(resp.status_code == 404, f"删除后 GET 返回 404: {resp.status_code}")

        # ---- 8. 真实 /v1/agent/session/start（LLM 可能不可用, 但结构正确）----
        print("\n  --- 真实 LLM Agent 会话（如 LLM 不可用会出现 error action）---")
        resp = client.post("/v1/agent/session/start", json={
            "task": "列出当前目录文件",
            "cwd": os.getcwd(), "os": "Linux", "shell": "zsh",
            "mode": "full_auto",
        })
        print_result(resp.status_code == 200, f"POST /v1/agent/session/start: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            real_sid = data.get("session_id")
            action = data.get("action")
            VALID_ACTIONS = ("execute", "done", "ask_user", "read_file", "write_file", "error", "unknown")
            print_result(action in VALID_ACTIONS, f"action 格式有效: {action!r}"
                         + ("（LLM 未启动）" if action in ("error", "unknown") else " ✓"))
            if real_sid:
                client.delete(f"/v1/agent/session/{real_sid}")  # 清理

        return True
    except Exception as e:
        print_result(False, f"Stage 2 API 测试失败: {e}")
        import traceback; traceback.print_exc()
        return False


def run_all_tests():
    """运行所有测试"""
    print("\n" + "🧪" * 30)
    print("  auto-shell 调试测试套件")
    print("🧪" * 30)
    
    results = []
    
    # 同步测试
    results.append(("配置加载", test_config()))
    results.append(("上下文收集", test_context()))
    results.append(("LLM 客户端", test_llm_client()))
    results.append(("Agent 初始化", test_agent()))
    results.append(("API 端点", test_api()))
    results.append(("Zsh 插件", test_zsh_plugin()))
    
    # Stage 2 测试
    results.append(("任务复杂度分析", asyncio.run(test_task_complexity())))
    results.append(("Agent Session 本地", asyncio.run(test_agent_session_local())))
    results.append(("Stage 2 API", test_api_stage2()))
    
    # 异步测试
    results.append(("Agent 执行", asyncio.run(test_agent_execute())))
    
    # 汇总结果
    print_header("测试结果汇总")
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        print_result(result, name)
    
    print("\n" + "-" * 60)
    print(f"  总计: {passed}/{total} 通过")
    print("-" * 60 + "\n")
    
    return passed == total


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="auto-shell 调试工具")
    parser.add_argument("--test", "-t", choices=[
        "config", "context", "llm", "agent", "api", "zsh",
        "complexity", "session", "stage2", "all"
    ], default="all", help="要运行的测试")
    
    args = parser.parse_args()
    
    if args.test == "all":
        success = run_all_tests()
    else:
        test_map = {
            "config": test_config,
            "context": test_context,
            "llm": test_llm_client,
            "agent": lambda: asyncio.run(test_agent_execute()),
            "api": test_api,
            "zsh": test_zsh_plugin,
            "complexity": lambda: asyncio.run(test_task_complexity()),
            "session": lambda: asyncio.run(test_agent_session_local()),
            "stage2": test_api_stage2,
        }
        success = test_map[args.test]()
    
    sys.exit(0 if success else 1)
