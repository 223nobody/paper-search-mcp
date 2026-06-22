<#
.SYNOPSIS
    paper-search-mcp 全局安装脚本 (Windows PowerShell)

.DESCRIPTION
    将 paper-search-mcp 注册到 Claude Code 全局 MCP 配置 (~/.claude/mcp.json)。

.PARAMETER Force
    强制覆盖已有的 paper-search-mcp 配置。

.PARAMETER Uninstall
    从全局配置中移除 paper-search-mcp。

.PARAMETER DryRun
    预览变更，不实际写入文件。

.EXAMPLE
    .\scripts\install-mcp-global.ps1              # 安装
    .\scripts\install-mcp-global.ps1 -Force        # 强制重新安装
    .\scripts\install-mcp-global.ps1 -Uninstall    # 卸载
    .\scripts\install-mcp-global.ps1 -DryRun       # 预览
#>

param(
    [switch]$Force,
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$PythonScript = Join-Path $ScriptDir "install-mcp-global.py"

if (-not (Test-Path $PythonScript)) {
    Write-Error "❌ 错误: 找不到 $PythonScript"
    exit 1
}

# 构建参数
$Args = @($PythonScript)
if ($Force)    { $Args += "--force" }
if ($Uninstall) { $Args += "--uninstall" }
if ($DryRun)   { $Args += "--dry-run" }

# 检测可用的 Python
$PythonCmd = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv run --directory $RepoRoot python @Args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python @Args
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    & python3 @Args
} else {
    Write-Error "❌ 错误: 找不到 Python。请安装 Python >=3.10 或 uv。"
    exit 1
}
