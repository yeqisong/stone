$ErrorActionPreference = 'Stop'

# 注册 kdaowatch:// 自定义协议 → 启动 WPF 悬浮窗
# 只需运行一次；之后网页按钮可通过 kdaowatch://open 调起桌面小窗。

$src = Join-Path $PSScriptRoot 'watch-window.ps1'
if (-not (Test-Path $src)) {
  throw "未找到 watch-window.ps1（应与本脚本同目录）"
}
$destDir = Join-Path $env:LOCALAPPDATA 'KdaoWatch'
New-Item -ItemType Directory -Force -Path $destDir | Out-Null
$destScript = Join-Path $destDir 'watch-window.ps1'
Copy-Item -Path $src -Destination $destScript -Force

$vbsSrc = Join-Path $PSScriptRoot 'launch-watch.vbs'
$destVbs = Join-Path $destDir 'launch-watch.vbs'
if (Test-Path $vbsSrc) { Copy-Item -Path $vbsSrc -Destination $destVbs -Force }

# 用 wscript 隐藏启动 PowerShell，避免控制台窗口；关闭 cmd 不会带走悬浮窗
$wscript = Join-Path $env:WINDIR 'System32\wscript.exe'
$command = '"{0}" //B //Nologo "{1}"' -f $wscript, $destVbs
$root = 'HKCU:\Software\Classes\kdaowatch'

New-Item -Path $root -Force | Out-Null
Set-Item -Path $root -Value 'URL:KdaoWatch Protocol'
New-ItemProperty -Path $root -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
New-Item -Path (Join-Path $root 'shell\open\command') -Force | Out-Null
Set-ItemProperty -Path (Join-Path $root 'shell\open\command') -Name '(default)' -Value $command

Write-Host 'kdaowatch:// 协议已注册。'
Write-Host ("启动命令: " + $command)
Write-Host '现在可以回到网页点击「桌面浮窗」。'
