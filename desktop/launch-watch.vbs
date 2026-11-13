' 无控制台启动 WPF 悬浮窗：WScript 立即退出，PowerShell/WPF 独立运行
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
script = dir & "\watch-window.ps1"
CreateObject("WScript.Shell").Run _
  "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & script & """", 0, False
