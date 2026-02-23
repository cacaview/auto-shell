#!/usr/bin/env zsh

# auto-shell Zsh 插件 (Stage 2)
# - 双击 Tab：获取命令建议 / 启动 Agent 会话
# - Ctrl+A：循环切换主模式 (suggest ↔ agent)
# - Ctrl+X,Ctrl+A：循环切换 Agent 子模式 (default→auto→full_auto)
# - preexec / precmd：自动上报命令结果、驱动 Agent 下一步

# 加载高精度时钟（EPOCHREALTIME）
zmodload zsh/datetime 2>/dev/null

# ============== 配置 ==============

AUTO_SHELL_DAEMON_URL="${AUTO_SHELL_DAEMON_URL:-http://127.0.0.1:28001}"
AUTO_SHELL_AGENT_MODE="${AUTO_SHELL_AGENT_MODE:-suggest}"
AUTO_SHELL_AGENT_SUBMODE="${AUTO_SHELL_AGENT_SUBMODE:-default}"

# 自动修正：若 URL 含路径（如被误设成 LLM API 地址），只保留 scheme://host:port
# 或指向非本机地址，均重置为 http://127.0.0.1:28001
function _auto_shell_fix_url() {
    local _scheme="${AUTO_SHELL_DAEMON_URL%%://*}"
    local _rest="${AUTO_SHELL_DAEMON_URL#*://}"
    local _host="${_rest%%/*}"
    local _clean="${_scheme}://${_host}"
    if [[ "$_clean" != "$AUTO_SHELL_DAEMON_URL" ]]; then
        echo "   ⚠️  AUTO_SHELL_DAEMON_URL 含多余路径，已自动修正: $AUTO_SHELL_DAEMON_URL → $_clean"
        AUTO_SHELL_DAEMON_URL="$_clean"
    fi
    if [[ "$AUTO_SHELL_DAEMON_URL" != *"127.0.0.1"* && \
          "$AUTO_SHELL_DAEMON_URL" != *"localhost"* && \
          "$AUTO_SHELL_DAEMON_URL" != *"::1"* ]]; then
        echo "   ⚠️  AUTO_SHELL_DAEMON_URL=$AUTO_SHELL_DAEMON_URL 指向非本机地址，已重置"
        echo "      如需永久修正请在 ~/.zshrc 中 unset AUTO_SHELL_DAEMON_URL 或设为 http://127.0.0.1:28001"
        AUTO_SHELL_DAEMON_URL="http://127.0.0.1:28001"
    fi
}
_auto_shell_fix_url
unfunction _auto_shell_fix_url

typeset -g _auto_shell_last_tab_time=0.0
typeset -g _auto_shell_double_tab_threshold="${AUTO_SHELL_DOUBLE_TAB_THRESHOLD:-0.4}"

typeset -g _auto_shell_session_id=""
typeset -g _auto_shell_session_task=""
typeset -g _auto_shell_session_active=0
typeset -g _auto_shell_pending_command=""

# ============== 工具函数 ==============

function _auto_shell_log() {
    [[ "${AUTO_SHELL_DEBUG:-0}" == "1" ]] && echo "[auto-shell] $*" >&2
}

function _auto_shell_jq_get() {
    local json="$1" key="$2"
    if command -v jq >/dev/null 2>&1; then
        # 用 printf 避免 echo 将 \n 解释为真实换行而破坏 JSON 解析
        printf '%s\n' "$json" | jq -r "$key" 2>/dev/null
    else
        printf '%s\n' "$json" | grep -o "\"${key##*.}\":[[:space:]]*\"[^\"]*\"" | head -1 | sed 's/.*": "\(.*\)"/\1/'
    fi
}

function _auto_shell_curl_post() {
    local url="$1" data="$2" timeout="${3:-30}"
    local _code _body
    # 用 -o 把 body 写到临时文件，彻底避免多行 body 破坏状态码提取
    _code=$(curl -s -w "%{http_code}" \
        -o /tmp/_auto_shell_resp.tmp \
        -X POST "$url" \
        -H "Content-Type: application/json" \
        -d "$data" --max-time "$timeout" 2>/dev/null)
    _body=$(cat /tmp/_auto_shell_resp.tmp 2>/dev/null)
    # 输出格式：body 内容 + 换行 + 状态码（状态码永远在最后一行）
    printf '%s\n%s' "$_body" "$_code"
}

# ============== 模式切换 ==============

_AUTO_SHELL_MODES=(suggest agent)
_AUTO_SHELL_SUBMODES=(default auto full_auto)

function _auto_shell_cycle_mode() {
    local i=0 idx=0
    for m in "${_AUTO_SHELL_MODES[@]}"; do
        [[ "$m" == "$AUTO_SHELL_AGENT_MODE" ]] && idx=$i
        (( i++ ))
    done
    idx=$(( (idx + 1) % ${#_AUTO_SHELL_MODES[@]} ))
    export AUTO_SHELL_AGENT_MODE="${_AUTO_SHELL_MODES[$idx]}"
    zle -M "🔄 主模式: $AUTO_SHELL_AGENT_MODE  子模式: $AUTO_SHELL_AGENT_SUBMODE"
    zle -R
}

function _auto_shell_cycle_submode() {
    local i=0 idx=0
    for m in "${_AUTO_SHELL_SUBMODES[@]}"; do
        [[ "$m" == "$AUTO_SHELL_AGENT_SUBMODE" ]] && idx=$i
        (( i++ ))
    done
    idx=$(( (idx + 1) % ${#_AUTO_SHELL_SUBMODES[@]} ))
    export AUTO_SHELL_AGENT_SUBMODE="${_AUTO_SHELL_SUBMODES[$idx]}"
    zle -M "🔄 Agent 子模式: $AUTO_SHELL_AGENT_SUBMODE"
    zle -R
}

# ============== 核心：双击 Tab ==============

function _auto_shell_handle_tab() {
    local current_time=${EPOCHREALTIME:-$(date +%s.%N)}
    local time_diff=$(( current_time - _auto_shell_last_tab_time ))
    _auto_shell_last_tab_time=$current_time

    if [[ -z "$BUFFER" ]]; then
        zle expand-or-complete
        return
    fi

    if (( time_diff < _auto_shell_double_tab_threshold )); then
        # 强制 ZLE 先全量重绘，同步终端坐标，避免后续 zle -M 错位
        zle -R
        if [[ "$_auto_shell_session_active" == "1" && -n "$_auto_shell_session_id" ]]; then
            _auto_shell_agent_get_next_suggestion
        elif [[ "$AUTO_SHELL_AGENT_MODE" == "agent" ]]; then
            _auto_shell_agent_start_session
        else
            _auto_shell_request_suggestion
        fi
    else
        zle expand-or-complete
    fi
}

# ============== 单次命令建议 ==============

function _auto_shell_request_suggestion() {
    local query="$BUFFER"
    zle -M "🤖 auto-shell 正在思考..."
    zle -R

    local json_data
    if command -v jq >/dev/null 2>&1; then
        json_data=$(jq -n \
            --arg q "$query" --arg c "$PWD" \
            --arg o "$(uname -s)" --arg s "zsh" \
            '{query: $q, cwd: $c, os: $o, shell: $s}')
    else
        json_data="{\"query\":\"${query//\"/\\\"}\",\"cwd\":\"${PWD//\"/\\\"}\",\"os\":\"$(uname -s)\",\"shell\":\"zsh\"}"
    fi

    local response http_code body
    response=$(_auto_shell_curl_post "$AUTO_SHELL_DAEMON_URL/v1/suggest" "$json_data")
    http_code=$(printf '%s\n' "$response" | tail -n1)
    body=$(printf '%s\n' "$response" | sed '$d')

    if [[ "$http_code" != "200" ]]; then
        zle -M "❌ auto-shell: 连接失败 (HTTP $http_code)"
        zle -R
        return
    fi

    local use_agent suggested_command is_dangerous
    use_agent=$(_auto_shell_jq_get "$body" ".use_agent")
    suggested_command=$(_auto_shell_jq_get "$body" ".command")
    is_dangerous=$(_auto_shell_jq_get "$body" ".is_dangerous")

    if [[ "$use_agent" == "true" ]]; then
        zle -M "🤖 任务较复杂，已切换到 Agent 模式 — 再次双击 Tab 启动"
        zle -R
        export AUTO_SHELL_AGENT_MODE="agent"
        return
    fi

    if [[ -n "$suggested_command" && "$suggested_command" != "null" ]]; then
        BUFFER="$suggested_command"
        CURSOR=${#BUFFER}
        if [[ "$is_dangerous" == "true" ]]; then
            zle -M "⚠️  此命令可能危险，请仔细检查！"
        else
            zle -M ""
        fi
    else
        zle -M "⚠️  auto-shell: 未能生成有效命令"
    fi
    zle -R
}

# ============== Agent 会话：启动 ==============

function _auto_shell_agent_start_session() {
    local task="$BUFFER"
    [[ -z "$task" ]] && { zle -M "⚠️  请先输入任务描述"; zle -R; return; }

    zle -M "🤖 [Agent] 启动会话..."
    zle -R

    local json_data
    if command -v jq >/dev/null 2>&1; then
        json_data=$(jq -n \
            --arg t "$task" --arg c "$PWD" \
            --arg o "$(uname -s)" --arg s "zsh" \
            --arg m "$AUTO_SHELL_AGENT_SUBMODE" \
            '{task: $t, cwd: $c, os: $o, shell: $s, mode: $m}')
    else
        json_data="{\"task\":\"${task//\"/\\\"}\",\"cwd\":\"${PWD//\"/\\\"}\",\"os\":\"$(uname -s)\",\"shell\":\"zsh\",\"mode\":\"${AUTO_SHELL_AGENT_SUBMODE}\"}"
    fi

    local response http_code body
    response=$(_auto_shell_curl_post "$AUTO_SHELL_DAEMON_URL/v1/agent/session/start" "$json_data" 60)
    http_code=$(printf '%s\n' "$response" | tail -n1)
    body=$(printf '%s\n' "$response" | sed '$d')

    if [[ "$http_code" != "200" ]]; then
        zle -M "❌ [Agent] 启动失败 (HTTP $http_code): $(echo $body | head -c 200)"
        zle -R
        return
    fi

    _auto_shell_session_id=$(_auto_shell_jq_get "$body" ".session_id")
    _auto_shell_session_task="$task"
    _auto_shell_session_active=1

    _auto_shell_apply_session_step "$body"
}

# ============== Agent 会话：请求下一步（手动触发）==============

function _auto_shell_agent_get_next_suggestion() {
    zle -M "🤖 [Agent] 请求下一步..."
    zle -R

    local json_data
    if command -v jq >/dev/null 2>&1; then
        json_data=$(jq -n --arg sid "$_auto_shell_session_id" '{session_id: $sid}')
    else
        json_data="{\"session_id\":\"${_auto_shell_session_id}\"}"
    fi

    local response http_code body
    response=$(_auto_shell_curl_post "$AUTO_SHELL_DAEMON_URL/v1/agent/session/step" "$json_data" 60)
    http_code=$(printf '%s\n' "$response" | tail -n1)
    body=$(printf '%s\n' "$response" | sed '$d')

    if [[ "$http_code" == "404" ]]; then
        zle -M "⚠️  [Agent] 会话已过期，请重新启动任务"
        zle -R
        _auto_shell_session_active=0; _auto_shell_session_id=""
        return
    fi
    [[ "$http_code" != "200" ]] && { zle -M "❌ [Agent] 步骤失败 (HTTP $http_code)"; zle -R; return; }

    _auto_shell_apply_session_step "$body"
}

# ============== 展示/应用一步结果 ==============

function _auto_shell_apply_session_step() {
    local body="$1"
    local action task_complete command is_dangerous needs_conf iteration final_msg

    action=$(_auto_shell_jq_get "$body" ".action")
    task_complete=$(_auto_shell_jq_get "$body" ".task_complete")
    command=$(_auto_shell_jq_get "$body" ".command")
    is_dangerous=$(_auto_shell_jq_get "$body" ".is_dangerous")
    needs_conf=$(_auto_shell_jq_get "$body" ".needs_confirmation")
    iteration=$(_auto_shell_jq_get "$body" ".iteration")
    final_msg=$(_auto_shell_jq_get "$body" ".final_message")

    if [[ "$task_complete" == "true" || "$action" == "done" ]]; then
        zle -M "✅ [Agent] 任务完成 (${iteration} 步): ${final_msg}"
        zle -R
        _auto_shell_session_active=0; _auto_shell_session_id=""; BUFFER=""
        return
    fi

    if [[ "$action" == "execute" && -n "$command" && "$command" != "null" ]]; then
        _auto_shell_pending_command="$command"
        BUFFER="$command"; CURSOR=${#BUFFER}
        local hint="[Agent 步骤 $iteration]"
        [[ "$is_dangerous" == "true" ]] && hint="$hint ⚠️ 危险"
        hint="$hint | 按 Enter 执行"
        [[ "$needs_conf" == "true" ]] && hint="$hint（需确认）"
        zle -M "$hint"
        zle -R
    elif [[ "$action" == "ask_user" ]]; then
        local question=$(_auto_shell_jq_get "$body" ".output")
        BUFFER=""; zle -M "🤖 [Agent] $question — 输入回答后按 Enter"
        zle -R
        _auto_shell_pending_command=""
    else
        zle -M "🤖 [Agent] 动作: $action — 双击 Tab 继续"
        zle -R
        _auto_shell_pending_command=""
    fi
}

# ============== preexec：记录被执行的命令 ==============

function _auto_shell_preexec() {
    local cmd="$1"
    # 如处于 Agent 会话，记录当前命令供 precmd 上报
    [[ "$_auto_shell_session_active" == "1" && -n "$cmd" ]] && \
        _auto_shell_pending_command="$cmd"

    # 非阻塞上报（普通模式也上报，维护上下文）
    [[ -n "$cmd" ]] && (
        curl -s -X POST "$AUTO_SHELL_DAEMON_URL/v1/command/result" \
            -H "Content-Type: application/json" \
            -d "{\"command\":\"${cmd//\"/\\\"}\",\"exit_code\":0}" \
            --max-time 5 >/dev/null 2>&1 &
    )
}

# ============== precmd：上报结果并驱动 Agent 下一步 ==============

function _auto_shell_precmd() {
    [[ "$_auto_shell_session_active" != "1" || -z "$_auto_shell_session_id" || \
       -z "$_auto_shell_pending_command" ]] && return

    local exit_code=${?:-0}
    local last_cmd="$_auto_shell_pending_command"
    _auto_shell_pending_command=""

    # 同步获取下一步（在 prompt 显示前完成）
    local json_data
    if command -v jq >/dev/null 2>&1; then
        json_data=$(jq -n \
            --arg sid "$_auto_shell_session_id" \
            --arg cmd "$last_cmd" \
            --argjson ec "$exit_code" \
            '{session_id: $sid, last_command: $cmd, last_exit_code: $ec}')
    else
        json_data="{\"session_id\":\"${_auto_shell_session_id}\",\"last_command\":\"${last_cmd//\"/\\\"}\",\"last_exit_code\":$exit_code}"
    fi

    local body
    body=$(curl -s -X POST "$AUTO_SHELL_DAEMON_URL/v1/agent/session/step" \
        -H "Content-Type: application/json" \
        -d "$json_data" --max-time 60 2>/dev/null)

    [[ -z "$body" ]] && return

    local task_complete action command iteration final_msg is_dangerous needs_conf
    task_complete=$(printf '%s\n' "$body" | jq -r '.task_complete' 2>/dev/null)
    action=$(printf '%s\n' "$body" | jq -r '.action' 2>/dev/null)
    command=$(printf '%s\n' "$body" | jq -r '.command // empty' 2>/dev/null)
    iteration=$(printf '%s\n' "$body" | jq -r '.iteration' 2>/dev/null)
    final_msg=$(printf '%s\n' "$body" | jq -r '.final_message' 2>/dev/null)
    is_dangerous=$(printf '%s\n' "$body" | jq -r '.is_dangerous' 2>/dev/null)
    needs_conf=$(printf '%s\n' "$body" | jq -r '.needs_confirmation' 2>/dev/null)

    echo ""
    if [[ "$task_complete" == "true" || "$action" == "done" ]]; then
        echo "✅ [auto-shell Agent] 任务完成: $final_msg"
        _auto_shell_session_active=0; _auto_shell_session_id=""
    elif [[ -n "$command" && "$command" != "null" ]]; then
        echo "🤖 [auto-shell Agent 步骤 $iteration] 建议命令:"
        echo "   $command"
        [[ "$is_dangerous" == "true" ]] && echo "   ⚠️  危险命令，请谨慎"
        [[ "$needs_conf" == "true" ]] && echo "   💬 双击 Tab 载入缓冲区确认"
        _auto_shell_pending_command="$command"
    else
        echo "🤖 [auto-shell Agent 步骤 $iteration] 动作: $action — 双击 Tab 继续"
    fi
}

# ============== 手动命令 ==============

function auto-shell-mode() {
    case "$1" in
        suggest|agent)
            export AUTO_SHELL_AGENT_MODE="$1"
            echo "🔄 主模式 → $AUTO_SHELL_AGENT_MODE"
            ;;
        default|auto|full_auto)
            export AUTO_SHELL_AGENT_SUBMODE="$1"
            echo "🔄 Agent 子模式 → $AUTO_SHELL_AGENT_SUBMODE"
            ;;
        status)
            echo "主模式:        $AUTO_SHELL_AGENT_MODE"
            echo "Agent 子模式:  $AUTO_SHELL_AGENT_SUBMODE"
            echo "Daemon:        $AUTO_SHELL_DAEMON_URL"
            echo "会话 ID:       ${_auto_shell_session_id:-无}"
            ;;
        stop)
            _auto_shell_session_active=0; _auto_shell_session_id=""
            echo "⏹️  Agent 会话已停止"
            ;;
        *)
            echo "用法: auto-shell-mode [suggest|agent|default|auto|full_auto|status|stop]"
            ;;
    esac
}

function auto-shell-start() {
    local port="${1:-28001}"
    curl -sf "$AUTO_SHELL_DAEMON_URL/health" >/dev/null 2>&1 && { echo "✅ Daemon 已在运行"; return 0; }
    echo "🚀 启动 Daemon (端口: $port)..."

    # 找 Python 可执行路径（支持 conda / venv / 系统 Python）
    local python_bin
    python_bin=$(command -v python3 || command -v python)
    [[ -z "$python_bin" ]] && { echo "❌ 找不到 Python，请手动启动"; return 1; }

    # 找项目根目录（插件文件的上级目录）
    local plugin_dir="${${(%):-%x}:A:h}"
    local project_dir="${plugin_dir:h}"

    nohup "$python_bin" -m uvicorn auto_shell.server:app \
        --host 127.0.0.1 --port "$port" --log-level warning \
        >/tmp/auto-shell-daemon.log 2>&1 &
    disown $!

    local i=0
    while (( i < 10 )); do
        sleep 0.5
        curl -sf "$AUTO_SHELL_DAEMON_URL/health" >/dev/null 2>&1 && { echo "✅ Daemon 启动成功"; return 0; }
        (( i++ ))
    done
    echo "❌ Daemon 启动超时，查看日志: /tmp/auto-shell-daemon.log"
    return 1
}

function auto-shell-stop() {
    pkill -f "auto_shell.server" 2>/dev/null && echo "🛑 Daemon 已停止"
}

function auto-shell-status() {
    if curl -s "$AUTO_SHELL_DAEMON_URL/health" >/dev/null 2>&1; then
        echo "✅ Daemon 运行中"
        curl -s "$AUTO_SHELL_DAEMON_URL/v1/agent/sessions" | jq . 2>/dev/null || \
            curl -s "$AUTO_SHELL_DAEMON_URL/v1/agent/sessions"
    else
        echo "❌ Daemon 未运行"
    fi
}

# ============== 插件自测 ==============

function auto-shell-test() {
    local passed=0 failed=0
    local url="$AUTO_SHELL_DAEMON_URL"

    _astest_ok()  { echo "  ✅ $1"; (( passed++ )); }
    _astest_fail(){ echo "  ❌ $1"; (( failed++ )); }
    _astest_h()   { echo "\n── $1 ──" }

    # ── 1. Daemon 连通性 ──────────────────────────────────────────
    _astest_h "1. Daemon 连通性"
    local health
    health=$(curl -sf "$url/health" 2>/dev/null)
    if [[ $? -eq 0 ]] && echo "$health" | grep -q '"ok"'; then
        _astest_ok "GET /health => ok"
    else
        _astest_fail "GET /health 失败（Daemon 未运行？运行 auto-shell-start 后重试）"
        echo "\n共 $passed 通过，$failed 失败"
        return 1
    fi

    # ── 2. _auto_shell_jq_get 解析 ────────────────────────────────
    _astest_h "2. JSON 解析工具函数"
    local sample='{"command":"ls -lh","is_dangerous":false,"use_agent":true}'
    local v1 v2 v3
    v1=$(_auto_shell_jq_get "$sample" ".command")
    v2=$(_auto_shell_jq_get "$sample" ".is_dangerous")
    v3=$(_auto_shell_jq_get "$sample" ".use_agent")
    [[ "$v1" == "ls -lh"  ]] && _astest_ok ".command = ls -lh"    || _astest_fail ".command 解析失败: $v1"
    [[ "$v2" == "false"   ]] && _astest_ok ".is_dangerous = false" || _astest_fail ".is_dangerous 解析失败: $v2"
    [[ "$v3" == "true"    ]] && _astest_ok ".use_agent = true"     || _astest_fail ".use_agent 解析失败: $v3"

    # ── 3. /v1/suggest 命令建议 ───────────────────────────────────
    _astest_h "3. POST /v1/suggest"
    local suggest_resp suggest_body suggest_code suggest_cmd
    suggest_resp=$(_auto_shell_curl_post "$url/v1/suggest" \
        '{"query":"列出当前目录","cwd":"/tmp","os":"Linux","shell":"zsh"}')
    suggest_code=$(printf '%s\n' "$suggest_resp" | tail -n1)
    suggest_body=$(printf '%s\n' "$suggest_resp" | sed '$d')
    if [[ "$suggest_code" == "200" ]]; then
        _astest_ok "HTTP 200"
        suggest_cmd=$(_auto_shell_jq_get "$suggest_body" ".command")
        if [[ -n "$suggest_cmd" && "$suggest_cmd" != "null" && "$suggest_cmd" != echo* ]]; then
            _astest_ok "command 非空: $suggest_cmd"
        elif [[ "$suggest_cmd" == echo* ]]; then
            _astest_fail "command 是 fallback echo（LLM 未返回有效命令）: $suggest_cmd"
        else
            _astest_fail "command 为空或 null"
        fi
        local use_agent
        use_agent=$(_auto_shell_jq_get "$suggest_body" ".use_agent")
        [[ "$use_agent" == "true" || "$use_agent" == "false" ]] \
            && _astest_ok "use_agent 字段存在: $use_agent" \
            || _astest_fail "use_agent 字段缺失"
    else
        _astest_fail "HTTP $suggest_code"
    fi

    # ── 4. /v1/suggest 复杂任务（应返回 use_agent=true）───────────
    _astest_h "4. POST /v1/suggest（复杂任务触发 Agent 模式）"
    local complex_resp complex_code complex_body complex_ua
    complex_resp=$(_auto_shell_curl_post "$url/v1/suggest" \
        '{"query":"帮我安装并配置 nginx，修改配置文件然后重启","cwd":"/tmp","os":"Linux","shell":"zsh"}')
    complex_code=$(printf '%s\n' "$complex_resp" | tail -n1)
    complex_body=$(printf '%s\n' "$complex_resp" | sed '$d')
    if [[ "$complex_code" == "200" ]]; then
        _astest_ok "HTTP 200"
        complex_ua=$(_auto_shell_jq_get "$complex_body" ".use_agent")
        [[ "$complex_ua" == "true" ]] \
            && _astest_ok "复杂任务 use_agent=true ✓" \
            || _astest_fail "复杂任务 use_agent=$complex_ua（期望 true）"
    else
        _astest_fail "HTTP $complex_code"
    fi

    # ── 5. Agent 会话（debug 模式，无需 LLM）──────────────────────
    _astest_h "5. Agent 会话 API（/debug/agent-session/...）"
    local sess_resp sess_code sess_body sess_id
    sess_resp=$(_auto_shell_curl_post "$url/debug/agent-session/start" \
        '{"task":"列出目录","cwd":"/tmp","os":"Linux","shell":"zsh","mode":"default"}' 10)
    sess_code=$(printf '%s\n' "$sess_resp" | tail -n1)
    sess_body=$(printf '%s\n' "$sess_resp" | sed '$d')
    if [[ "$sess_code" == "200" ]]; then
        _astest_ok "debug session/start HTTP 200"
        sess_id=$(_auto_shell_jq_get "$sess_body" ".session_id")
        local s_action s_iter
        s_action=$(_auto_shell_jq_get "$sess_body" ".action")
        s_iter=$(_auto_shell_jq_get "$sess_body" ".iteration")
        [[ -n "$sess_id" && "$sess_id" != "null" ]] \
            && _astest_ok "session_id: $sess_id" \
            || _astest_fail "session_id 为空"
        [[ -n "$s_action" && "$s_action" != "null" ]] \
            && _astest_ok "action: $s_action, iteration: $s_iter" \
            || _astest_fail "action 字段缺失"

        # step
        if [[ -n "$sess_id" && "$sess_id" != "null" ]]; then
            local step_resp step_code step_body
            step_resp=$(_auto_shell_curl_post "$url/debug/agent-session/step" \
                "{\"session_id\":\"$sess_id\",\"last_command\":\"ls\",\"last_exit_code\":0}" 10)
            step_code=$(printf '%s\n' "$step_resp" | tail -n1)
            step_body=$(printf '%s\n' "$step_resp" | sed '$d')
            [[ "$step_code" == "200" ]] \
                && _astest_ok "debug session/step HTTP 200" \
                || _astest_fail "debug session/step HTTP $step_code"
        fi
    else
        _astest_fail "debug session/start HTTP $sess_code"
    fi

    # ── 6. 真实 Agent 会话 ─────────────────────────────────────────
    _astest_h "6. 真实 Agent 会话（POST /v1/agent/session/start）"
    local rsess_resp rsess_code rsess_body rsess_id
    rsess_resp=$(_auto_shell_curl_post "$url/v1/agent/session/start" \
        '{"task":"查找大文件","cwd":"/tmp","os":"Linux","shell":"zsh","mode":"default"}' 60)
    rsess_code=$(printf '%s\n' "$rsess_resp" | tail -n1)
    rsess_body=$(printf '%s\n' "$rsess_resp" | sed '$d')
    if [[ "$rsess_code" == "200" ]]; then
        _astest_ok "HTTP 200"
        rsess_id=$(_auto_shell_jq_get "$rsess_body" ".session_id")
        local r_action r_cmd
        r_action=$(_auto_shell_jq_get "$rsess_body" ".action")
        r_cmd=$(_auto_shell_jq_get "$rsess_body" ".command")
        [[ -n "$rsess_id" && "$rsess_id" != "null" ]] \
            && _astest_ok "session_id: $rsess_id" \
            || _astest_fail "session_id 为空"
        [[ "$r_action" == "execute" || "$r_action" == "ask_user" || "$r_action" == "done" ]] \
            && _astest_ok "action=$r_action command=${r_cmd:-(无，符合预期)}" \
            || _astest_fail "action=$r_action（期望 execute / ask_user / done）"

        # 清理会话
        if [[ -n "$rsess_id" && "$rsess_id" != "null" ]]; then
            local del_code
            del_code=$(curl -sf -o /dev/null -w "%{http_code}" \
                -X DELETE "$url/v1/agent/session/$rsess_id" 2>/dev/null)
            [[ "$del_code" == "200" ]] \
                && _astest_ok "DELETE session HTTP 200" \
                || _astest_fail "DELETE session HTTP $del_code"
        fi
    else
        _astest_fail "HTTP $rsess_code（body: $(echo $rsess_body | head -c 120)）"
    fi

    # ── 7. EPOCHREALTIME 双击 Tab 计时精度 ────────────────────────
    _astest_h "7. EPOCHREALTIME 精度（双击 Tab 计时）"
    local t1 t2 diff
    t1=${EPOCHREALTIME:-0}
    sleep 0.05
    t2=${EPOCHREALTIME:-0}
    diff=$(( t2 - t1 ))
    (( diff > 0.03 && diff < 0.5 )) \
        && _astest_ok "EPOCHREALTIME 精度正常: diff=${diff}s" \
        || _astest_fail "EPOCHREALTIME 异常: diff=${diff}s（可能不支持）"

    # ── 汇总 ──────────────────────────────────────────────────────
    echo ""
    echo "════════════════════════════════"
    echo "  共 $(( passed + failed )) 项   ✅ $passed 通过   ❌ $failed 失败"
    echo "════════════════════════════════"
    unfunction _astest_ok _astest_fail _astest_h 2>/dev/null
    return $(( failed > 0 ? 1 : 0 ))
}

# ============== ZLE & Hooks 注册 ==============

zle -N _auto_shell_handle_tab
zle -N _auto_shell_cycle_mode
zle -N _auto_shell_cycle_submode

bindkey '^I'   _auto_shell_handle_tab     # Tab
bindkey '^A'   _auto_shell_cycle_mode     # Ctrl+A
bindkey '^X^A' _auto_shell_cycle_submode  # Ctrl+X Ctrl+A

autoload -Uz add-zsh-hook
add-zsh-hook preexec _auto_shell_preexec 2>/dev/null || true
add-zsh-hook precmd  _auto_shell_precmd  2>/dev/null || true

print -P "%F{green}🚀 auto-shell Stage 2 插件已加载%f"
print -P "   双击 Tab    获取命令建议 / 推进 Agent 步骤"
print -P "   Ctrl+A      切换主模式 %B(当前: $AUTO_SHELL_AGENT_MODE)%b"
print -P "   Ctrl+X,A    切换 Agent 子模式 %B(当前: $AUTO_SHELL_AGENT_SUBMODE)%b"
print -P "   Daemon:     %U$AUTO_SHELL_DAEMON_URL%u"

# 自动检测 Daemon，未运行时尝试后台启动
if ! curl -sf "$AUTO_SHELL_DAEMON_URL/health" >/dev/null 2>&1; then
    echo "   ⚡ Daemon 未运行，正在后台启动..."
    typeset -g _as_python_bin
    _as_python_bin=$(command -v python3 || command -v python)
    if [[ -n "$_as_python_bin" ]]; then
        typeset -g _as_plugin_dir="${${(%):-%x}:A:h}"
        (cd "${_as_plugin_dir:h}" && \
         nohup "$_as_python_bin" -m uvicorn auto_shell.server:app \
             --host 127.0.0.1 --port 28001 --log-level warning \
             >/tmp/auto-shell-daemon.log 2>&1 &) 2>/dev/null
    else
        echo "   ⚠️  找不到 Python，请手动运行 auto-shell-start"
    fi
fi
