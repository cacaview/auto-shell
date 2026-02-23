# auto-shell

> **终端即为聊天框** — 在 Zsh 里输入自然语言，双击 Tab 直接得到可执行命令。

AI 驱动的 Shell 命令生成工具。架构：本地 Python Daemon（FastAPI）+ 原生 Zsh 插件，无 PTY 劫持，无感融入现有工作流。

---

## 功能亮点

- **双击 Tab 触发**：在命令行输入中文（或英文）描述，双击 Tab 将其替换为可执行命令
- **智能路由**：简单任务直接给命令；复杂多步任务自动切换为 Agent 模式
- **Agent 会话模式**：持续建议 → 用户双击 Tab 确认 → 执行结果自动反馈 → AI 给出下一步（结对编程体验）
- **三种安全等级**：Default（每步确认）/ Auto（AI 判断危险性）/ Full-Auto（全自动）
- **OpenAI 兼容 API**：支持 ChatGPT、GLM、Llama 等任何兼容 OpenAI 格式的模型
- **轻量无侵入**：仅 hook `Tab` / `Ctrl+A` / `Ctrl+X,A`，不修改 `PS1`，不包装 PTY

---

## 架构

```
用户在 Zsh 输入自然语言
        │
        │ 双击 Tab
        ▼
┌──────────────────┐       HTTP (127.0.0.1:28001)       ┌─────────────────────┐
│  Zsh 插件        │ ─────────────────────────────────► │  Python Daemon       │
│  auto-shell      │                                    │  (FastAPI + uvicorn) │
│  .plugin.zsh     │ ◄───────────────────────────────── │                      │
└──────────────────┘       command / session step       └─────────┬───────────┘
                                                                   │ OpenAI API
                                                                   ▼
                                                        ┌─────────────────────┐
                                                        │  LLM (GLM / GPT 等)  │
                                                        │  Function Calling    │
                                                        └─────────────────────┘
```

---

## 环境要求

| 组件 | 版本要求 |
|------|---------|
| Python | ≥ 3.11 |
| Zsh | ≥ 5.8 |
| `jq` | 任意版本（用于 JSON 解析，推荐安装） |
| `curl` | 任意版本 |

---

## 安装

```bash
# 1. 克隆项目
git clone https://github.com/yourname/auto-shell.git
cd auto-shell

# 2. 安装依赖（推荐 conda 或 venv）
pip install -e .

# 3. 复制并编辑配置
cp config.yaml.example config.yaml   # 若有模版；否则直接编辑 config.yaml
```

### 加载 Zsh 插件

**方式一：直接 source（临时，适合测试）**
```zsh
source /path/to/auto-shell/plugin/auto-shell.plugin.zsh
```

**方式二：写入 `~/.zshrc`（永久）**
```zsh
# 在 ~/.zshrc 末尾追加：
unset AUTO_SHELL_DAEMON_URL    # 清除可能错误的历史值
source /path/to/auto-shell/plugin/auto-shell.plugin.zsh
```

**方式三：oh-my-zsh 自定义插件目录**
```zsh
ln -s /path/to/auto-shell/plugin \
      ~/.oh-my-zsh/custom/plugins/auto-shell
# 在 ~/.zshrc 的 plugins 列表中加入 auto-shell
```

---

## 配置

编辑 `config.yaml`：

```yaml
llm:
  api_base: "http://127.0.0.1:8000/v1"   # OpenAI 兼容 API 地址
  api_key:  "sk-your-api-key"
  model:    "gpt-4o-mini"                 # 或 glm-4、qwen-turbo 等
  temperature: 0.1
  max_tokens:  200                        # 命令生成用；内部 agent 使用 1024

daemon:
  host: "127.0.0.1"
  port: 28001

agent:
  default_mode: "default"                 # default | auto | full_auto
  max_iterations: 10
  dangerous_commands:                     # 需要用户确认的命令前缀（正则）
    - "^rm"
    - "^sudo"
    - "^chmod"
    - "^dd"

shell:
  double_tab_threshold: 400              # 双击 Tab 判定窗口（毫秒）
  request_timeout: 30
```

> **环境变量优先级高于 config.yaml**（后续版本实现，当前以文件为准）

---

## 使用方法

### 1. 启动 Daemon

```zsh
# 前台运行（开发调试）
python -m uvicorn auto_shell.server:app \
    --host 127.0.0.1 --port 28001

# 后台运行
nohup python -m uvicorn auto_shell.server:app \
    --host 127.0.0.1 --port 28001 --log-level warning \
    > /tmp/auto-shell.log 2>&1 &

# 或使用插件内置命令（自动检测并后台启动）
auto-shell-start
```

### 2. 获取命令建议（普通模式）

在 Zsh 命令行输入自然语言描述，然后**快速双击 Tab**：

```
(base) ~ % 查找当前目录下最大的20个文件<Tab><Tab>
→ find . -type f -printf '%s %p\n' | sort -nr | head -20
```

按 **Enter** 执行，按 **Esc** 取消。

### 3. Agent 多步任务模式

复杂任务（如"安装并配置 nginx 然后重启"）会自动触发 Agent 模式提示：

```
→ 🤖 任务较复杂，已切换到 Agent 模式 — 再次双击 Tab 启动
```

再次双击 Tab 启动会话：

```
→ [Agent 步骤 1] | 按 Enter 执行
BUFFER: apt install -y nginx
```

按 Enter 执行后，结果自动上报 AI，AI 给出下一步，直到任务完成：

```
✅ [auto-shell Agent] 任务完成: nginx 已安装并启动
```

---

## 快捷键

| 按键 | 动作 |
|------|------|
| **Tab Tab**（双击） | 获取命令建议 / 推进 Agent 步骤 |
| **Ctrl + A** | 循环切换主模式：`suggest` ↔ `agent` |
| **Ctrl + X, Ctrl + A** | 循环切换 Agent 子模式：`default` → `auto` → `full_auto` |
| **Esc** | 取消/清空当前建议（Zsh 默认行为） |

---

## Agent 运行模式

| 模式 | 说明 | 命令确认 |
|------|------|---------|
| `default` | 每步都放入缓冲区等待用户按 Enter | 全部 |
| `auto` | AI 自动判断安全性 | 仅高危命令 |
| `full_auto` | 无拦截，全部自动执行（⚠️ 慎用） | 无 |

切换方式：
```zsh
auto-shell-mode agent        # 主模式切换到 agent
auto-shell-mode full_auto    # Agent 子模式切换到 full_auto
auto-shell-mode status       # 查看当前状态
auto-shell-mode stop         # 停止当前 Agent 会话
```

---

## 管理命令

```zsh
auto-shell-start             # 后台启动 Daemon（若已运行则跳过）
auto-shell-stop              # 停止 Daemon
auto-shell-status            # 查看 Daemon 状态及活跃会话
auto-shell-test              # 运行插件自测（18 项，覆盖所有核心功能）
auto-shell-mode status       # 查看当前模式与 Daemon 地址
```

---

## API 参考

Daemon 启动后可访问交互式文档：`http://127.0.0.1:28001/docs`

| 端点 | 方法 | 描述 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/config` | GET | 读取当前配置 |
| `/config/reload` | POST | 热重载 `config.yaml` |
| `/v1/suggest` | POST | 命令建议（含智能路由） |
| `/v1/suggest/stream` | POST | 流式命令建议（SSE） |
| `/v1/agent` | POST | 一次性 Agent 执行（旧接口） |
| `/v1/agent/session/start` | POST | 启动 Agent 会话，返回第一步 |
| `/v1/agent/session/step` | POST | 上报执行结果，获取下一步 |
| `/v1/agent/session/{id}` | GET | 查询会话状态 |
| `/v1/agent/session/{id}` | DELETE | 删除/取消会话 |
| `/v1/agent/sessions` | GET | 列出所有活跃会话 |
| `/v1/command/result` | POST | 上报命令执行结果（上下文维护） |
| `/debug/mock-suggest` | POST | Mock 建议（不调用 LLM） |
| `/debug/agent-session/start` | POST | Mock Agent 会话（预设脚本） |
| `/debug/agent-session/step` | POST | Mock Agent 步骤推进 |

**`POST /v1/suggest` 示例：**
```bash
curl -s -X POST http://127.0.0.1:28001/v1/suggest \
  -H "Content-Type: application/json" \
  -d '{"query":"查找大文件","cwd":"/home/user","os":"Linux","shell":"zsh"}' | jq .
```
```json
{
  "command": "find . -type f -size +100M -exec ls -lh {} \\;",
  "explanation": "",
  "is_dangerous": false,
  "use_agent": false
}
```

---

## 项目结构

```
auto-shell/
├── auto_shell/
│   ├── config.py        # 配置管理（YAML + 环境变量）
│   ├── context.py       # 终端上下文收集器
│   ├── llm_client.py    # OpenAI 兼容 LLM 客户端（Function Calling）
│   ├── agent.py         # Agent 引擎：ReAct 循环 / Session 管理 / 复杂度分析
│   ├── server.py        # FastAPI Daemon（所有 HTTP 端点）
│   └── cli.py           # 命令行入口（`auto-shell` 命令）
├── plugin/
│   └── auto-shell.plugin.zsh   # Zsh 插件（双击 Tab / ZLE / Hook）
├── tests/
│   └── test_auto_shell.py      # pytest 测试套件
├── debug_tools.py       # 快速调试脚本
├── config.yaml          # 主配置文件
└── pyproject.toml       # 项目依赖声明
```

---

## 开发 & 调试

```bash
# 运行单元测试
pytest tests/ -v

# 插件自测（需 Daemon 运行中）
source plugin/auto-shell.plugin.zsh
auto-shell-test

# 查看 Daemon 日志
tail -f /tmp/auto-shell.log

# 验证 LLM 连通性
python debug_tools.py
```

### 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| HTTP 000 | Daemon 未运行 | 运行 `auto-shell-start` |
| 命令返回中文解释 | LLM 未触发 Function Calling | 检查 `max_tokens` 是否 ≥ 1024（内部已硬编码） |
| `AUTO_SHELL_DAEMON_URL` 为 LLM 地址 | 历史环境变量残留 | 在 `~/.zshrc` 中 `unset AUTO_SHELL_DAEMON_URL`，插件会自动修正并提示 |
| 双击 Tab 后终端显示错位 | ZLE 坐标不同步 | 已修复（每次操作前 `zle -R` 同步坐标） |
| `local: not local` 警告 | zsh 版本问题 | 已修复（改为函数内使用 `local`） |

---

## 路线图

- [x] Stage 0：PoC — Daemon + 基础 API + 插件原型
- [x] Stage 1：MVP — 上下文收集 + 双击 Tab 替换输入行
- [x] Stage 2：Agent 模式 — ReAct 循环 + 会话管理 + 安全等级
- [ ] Stage 3：体验优化
  - [ ] 交互式命令处理（Vim / Git 等）的 TTY 透传 + 降级提示
  - [ ] 命令执行后异步展示解释（学习模式）
  - [ ] 一键安装脚本 + 自动配置 `~/.zshrc`
  - [ ] Bash 适配

---

## License

MIT
