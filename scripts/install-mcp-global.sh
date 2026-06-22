#!/usr/bin/env bash
# =============================================================================
# paper-search-mcp 全局安装脚本 (Bash / Git Bash / Mac / Linux)
#
# 将 paper-search-mcp 注册到 Claude Code 全局 MCP 配置。
# 用法:
#     bash scripts/install-mcp-global.sh          # 安装
#     bash scripts/install-mcp-global.sh --force     # 强制重新安装
#     bash scripts/install-mcp-global.sh --uninstall # 卸载
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 使用 Python 安装脚本作为核心实现
PYTHON_SCRIPT="$SCRIPT_DIR/install-mcp-global.py"

if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "❌ 错误: 找不到 $PYTHON_SCRIPT"
    exit 1
fi

# 检测可用的 Python（优先使用 uv 环境中的，其次系统 Python3）
if command -v uv &>/dev/null && [ -d "$REPO_ROOT/.venv" ]; then
    PYTHON_CMD=(uv run --directory "$REPO_ROOT" python)
elif command -v python3 &>/dev/null; then
    PYTHON_CMD=(python3)
elif command -v python &>/dev/null; then
    PYTHON_CMD=(python)
else
    echo "❌ 错误: 找不到 Python。请安装 Python >=3.10 或 uv。"
    exit 1
fi

exec "${PYTHON_CMD[@]}" "$PYTHON_SCRIPT" "$@"
