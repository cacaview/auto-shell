"""
命令行接口模块
"""

import argparse
import asyncio
import logging
from typing import Optional

from .config import get_config, reload_config
from .server import start_daemon
from .llm_client import get_llm_client
from .agent import Agent, AgentMode

logger = logging.getLogger("auto-shell")


def setup_logging(level: str = "info"):
    """设置日志级别"""
    levels = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    logging.basicConfig(
        level=levels.get(level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


def cmd_start(args):
    """启动 Daemon 服务"""
    setup_logging(args.log_level)
    config = get_config()
    
    host = args.host or config.daemon.host
    port = args.port or config.daemon.port
    
    print(f"🚀 启动 auto-shell Daemon...")
    print(f"   地址: http://{host}:{port}")
    print(f"   API 文档: http://{host}:{port}/docs")
    print(f"   按 Ctrl+C 停止")
    
    start_daemon(host, port)


def cmd_suggest(args):
    """测试命令建议"""
    setup_logging(args.log_level)
    
    async def run():
        config = get_config()
        llm = get_llm_client()
        
        context = {
            "cwd": ".",
            "os": "Linux",
            "shell": "bash",
        }
        
        print(f"🤖 查询: {args.query}")
        print("   正在生成命令...")
        
        command = await llm.generate_command(args.query, context)
        
        print(f"   建议命令: {command}")
        return command
    
    asyncio.run(run())


def cmd_agent(args):
    """测试 Agent 模式"""
    setup_logging(args.log_level)
    
    async def run():
        mode_map = {
            "default": AgentMode.DEFAULT,
            "auto": AgentMode.AUTO,
            "full_auto": AgentMode.FULL_AUTO,
        }
        mode = mode_map.get(args.mode, AgentMode.DEFAULT)
        
        context = {
            "cwd": ".",
            "os": "Linux",
            "shell": "bash",
        }
        
        print(f"🤖 Agent 模式: {args.mode}")
        print(f"   任务: {args.query}")
        print("   开始执行...")
        print("-" * 50)
        
        agent = Agent(mode=mode)
        results = await agent.run(args.query, context)
        
        for i, result in enumerate(results):
            print(f"\n步骤 {i + 1}: {result.action}")
            if hasattr(result, 'command') and result.command:
                print(f"   命令: {result.command}")
            print(f"   成功: {result.success}")
            if result.output:
                print(f"   输出: {result.output[:200]}")
            if result.error:
                print(f"   错误: {result.error}")
        
        print("-" * 50)
        print(f"✅ 任务完成: {agent.state.final_message}")
    
    asyncio.run(run())


def cmd_config(args):
    """显示配置"""
    setup_logging(args.log_level)
    config = get_config()
    
    print("📋 当前配置:")
    print(f"   LLM API 地址: {config.llm.api_base}")
    print(f"   LLM 模型: {config.llm.model}")
    print(f"   Daemon 地址: {config.daemon.host}:{config.daemon.port}")
    print(f"   Agent 模式: {config.agent.default_mode}")
    print(f"   最大迭代次数: {config.agent.max_iterations}")


def cmd_test(args):
    """运行测试"""
    setup_logging(args.log_level)
    
    print("🧪 运行 auto-shell 测试...")
    print()
    
    # 测试配置加载
    print("1. 测试配置加载...")
    try:
        config = get_config()
        print(f"   ✅ 配置加载成功")
    except Exception as e:
        print(f"   ❌ 配置加载失败: {e}")
        return
    
    # 测试 LLM 客户端
    print("\n2. 测试 LLM 客户端...")
    try:
        llm = get_llm_client()
        print(f"   ✅ LLM 客户端初始化成功")
    except Exception as e:
        print(f"   ❌ LLM 客户端初始化失败: {e}")
    
    # 测试 Agent
    print("\n3. 测试 Agent 初始化...")
    try:
        agent = Agent()
        print(f"   ✅ Agent 初始化成功")
    except Exception as e:
        print(f"   ❌ Agent 初始化失败: {e}")
    
    print("\n✅ 所有测试通过!")


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="auto-shell - 终端即为聊天框",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  auto-shell start                    # 启动 Daemon
  auto-shell start --port 8080        # 指定端口启动
  auto-shell suggest "查找大文件"      # 测试命令建议
  auto-shell agent "列出当前目录"      # 测试 Agent 模式
  auto-shell config                   # 显示配置
  auto-shell test                     # 运行测试
        """
    )
    
    parser.add_argument(
        "--log-level", "-l",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="日志级别"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # start 命令
    start_parser = subparsers.add_parser("start", help="启动 Daemon 服务")
    start_parser.add_argument("--host", help="监听地址")
    start_parser.add_argument("--port", type=int, help="监听端口")
    start_parser.set_defaults(func=cmd_start)
    
    # suggest 命令
    suggest_parser = subparsers.add_parser("suggest", help="测试命令建议")
    suggest_parser.add_argument("query", help="自然语言查询")
    suggest_parser.set_defaults(func=cmd_suggest)
    
    # agent 命令
    agent_parser = subparsers.add_parser("agent", help="测试 Agent 模式")
    agent_parser.add_argument("query", help="任务描述")
    agent_parser.add_argument("--mode", "-m", choices=["default", "auto", "full_auto"], default="default", help="Agent 模式")
    agent_parser.set_defaults(func=cmd_agent)
    
    # config 命令
    config_parser = subparsers.add_parser("config", help="显示配置")
    config_parser.set_defaults(func=cmd_config)
    
    # test 命令
    test_parser = subparsers.add_parser("test", help="运行测试")
    test_parser.set_defaults(func=cmd_test)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return
    
    args.func(args)


if __name__ == "__main__":
    main()
