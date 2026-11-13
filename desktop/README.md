# K道 WPF 透明悬浮盯盘（Windows）

Chrome 的 Document PiP 窗口是**不透明浏览器窗口**，CSS 无法透出桌面。  
这个 WPF 小工具用 `WindowStyle=None + AllowsTransparency=True + Topmost=True` 做到真正的透明、无边框、桌面置顶。

## 运行

后端和前端需要先启动（默认 `:8000` / `:3000`）。

在 Windows PowerShell 中：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "\\wsl.localhost\Ubuntu\home\bnbnyu\projects\stone\desktop\watch-window.ps1"
```

也可以把 `watch-window.ps1` 和 `watch-window.cmd` 复制到 Windows 桌面，双击 `watch-window.cmd`。

首次运行会弹出 Windows 凭据框，输入 K道登录账号（默认用户名 `admin`，密码见 `.env` 的 `LOGIN_PASSWORD`）。  
Token 保存在 `%APPDATA%\KdaoWatch\token.txt`，约 24 小时有效；过期后重新登录。

## 操作

- 拖动卡片：按住卡片拖动
- 点击卡片：默认浏览器打开 `http://127.0.0.1:3000/#/detail/<代码>`
- 拖动空白处：移动整个悬浮窗
- 双击空白处：整理卡片布局
- 右键：整理布局 / 退出
- Esc：退出

窗口按卡片数量自动计算最小宽高（190×116 卡片、10px 间距、`ceil(sqrt(n))` 列）。

## 参数

```powershell
.\watch-window.ps1 `
  -ApiBase http://127.0.0.1:8000 `
  -WebBase http://127.0.0.1:3000 `
  -Username admin
```

如果 Windows 访问不到 WSL 的 localhost，把 `-ApiBase` / `-WebBase` 换成 WSL 的 IP（`ip addr` 查看）。

## 说明

- 只用 Windows 自带的 Windows PowerShell 5.1 + WPF，不需要 .NET SDK、Electron 或浏览器扩展。
- 透明窗口依赖 Windows 桌面合成（Windows 10/11 正常开启）。
- 开机自启：把 `watch-window.cmd` 的快捷方式放进 `shell:startup`。
- Token 以明文保存在当前 Windows 用户的 `%APPDATA%`，仅本机使用。
