param(
  [string]$ApiBase = 'http://127.0.0.1:8000',
  [string]$WebBase = 'http://127.0.0.1:3000',
  [string]$Username = 'admin',
  [string]$Token = '',
  [switch]$SelfTest,
  [int]$RunSeconds = 0
)

# K道 WPF 透明悬浮盯盘：
# - WindowStyle=None + AllowsTransparency=True + Topmost=True
# - 只显示持仓卡片；卡片可拖动、点击打开浏览器详情
# - 数据复用 /api/portfolio/watch-window（5s 轮询）

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

$AppDir = Join-Path $env:APPDATA 'KdaoWatch'
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
$TokenFile = Join-Path $AppDir 'token.txt'
$PosFile = Join-Path $AppDir 'positions.json'

$script:token = $Token
$script:cards = @{}
$script:positions = @{}
$script:drag = $null
$script:placed = $false
$script:window = $null
$script:canvas = $null

$bc = New-Object System.Windows.Media.BrushConverter
$script:cardBrush = $bc.ConvertFromString('#E61A1C20')
$script:cardBorder = $bc.ConvertFromString('#33FFFFFF')
$script:upBrush = $bc.ConvertFromString('#FF5C6C')
$script:downBrush = $bc.ConvertFromString('#2ED47A')
$script:textBrush = $bc.ConvertFromString('#F2F4F8')
$script:dimBrush = $bc.ConvertFromString('#9AA3B2')

function Load-Positions {
  $script:positions = @{}
  if (Test-Path $PosFile) {
    try {
      $j = Get-Content -Raw -Encoding UTF8 $PosFile | ConvertFrom-Json
      foreach ($p in $j.PSObject.Properties) {
        $script:positions[$p.Name] = @{ x = [double]$p.Value.x; y = [double]$p.Value.y }
      }
    } catch { }
  }
}

function Save-Positions {
  try {
    $obj = @{}
    foreach ($code in $script:cards.Keys) {
      $b = $script:cards[$code].Border
      $obj[$code] = @{ x = [double][System.Windows.Controls.Canvas]::GetLeft($b);
                       y = [double][System.Windows.Controls.Canvas]::GetTop($b) }
    }
    $obj | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $PosFile
  } catch { }
}

function Login {
  if ($script:token) { return $script:token }
  if (Test-Path $TokenFile) {
    try {
      $script:token = ((Get-Content -Raw -Encoding UTF8 $TokenFile) -replace "^\uFEFF", '').Trim()
    } catch { }
  }
  if ($script:token) { return $script:token }
  $cred = Get-Credential -UserName $Username -Message 'K道浮窗登录'
  if ($null -eq $cred) { throw '登录已取消' }
  $body = @{
    username = ($cred.UserName -replace '^.*\\', '')
    password = $cred.GetNetworkCredential().Password
  } | ConvertTo-Json
  $res = Invoke-RestMethod -Method Post -Uri "$ApiBase/api/login" `
    -ContentType 'application/json' -Body $body -TimeoutSec 10
  $script:token = $res.token
  Set-Content -NoNewline -Encoding UTF8 $TokenFile $script:token
  return $script:token
}

function Get-WatchData {
  $t = Login
  $headers = @{ Authorization = "Bearer $t" }
  try {
    return Invoke-RestMethod -Uri "$ApiBase/api/portfolio/watch-window" -Headers $headers -TimeoutSec 6
  } catch {
    $status = 0
    if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    if ($status -eq 401) {
      Remove-Item $TokenFile -ErrorAction SilentlyContinue
      $script:token = $null
      $t = Login
      $headers = @{ Authorization = "Bearer $t" }
      return Invoke-RestMethod -Uri "$ApiBase/api/portfolio/watch-window" -Headers $headers -TimeoutSec 6
    }
    throw
  }
}

function New-Card($item) {
  $border = New-Object System.Windows.Controls.Border
  $border.Width = 190
  $border.Height = 116
  $border.CornerRadius = New-Object System.Windows.CornerRadius -ArgumentList 8
  $border.Background = $script:cardBrush
  $border.BorderBrush = $script:cardBorder
  $border.BorderThickness = New-Object System.Windows.Thickness -ArgumentList 1
  $border.Cursor = [System.Windows.Input.Cursors]::Hand
  $border.Tag = [string]$item.code

  $grid = New-Object System.Windows.Controls.Grid
  foreach ($h in @(22, 32, 18, 34)) {
    $rd = New-Object System.Windows.Controls.RowDefinition
    $rd.Height = New-Object System.Windows.GridLength -ArgumentList $h
    $grid.RowDefinitions.Add($rd)
  }

  $head = New-Object System.Windows.Controls.StackPanel
  $head.Orientation = [System.Windows.Controls.Orientation]::Horizontal
  $head.Margin = New-Object System.Windows.Thickness -ArgumentList 8, 4, 8, 0
  $name = New-Object System.Windows.Controls.TextBlock
  $name.Width = 132
  $name.FontSize = 12
  $name.FontWeight = [System.Windows.FontWeights]::SemiBold
  $name.Foreground = $script:textBrush
  $name.TextTrimming = [System.Windows.TextTrimming]::CharacterEllipsis
  $code = New-Object System.Windows.Controls.TextBlock
  $code.FontSize = 10
  $code.Foreground = $script:dimBrush
  $code.HorizontalAlignment = 'Right'
  $head.Children.Add($name) | Out-Null
  $head.Children.Add($code) | Out-Null
  [System.Windows.Controls.Grid]::SetRow($head, 0)
  $grid.Children.Add($head) | Out-Null

  $price = New-Object System.Windows.Controls.TextBlock
  $price.FontSize = 22
  $price.FontWeight = [System.Windows.FontWeights]::Bold
  $price.Margin = New-Object System.Windows.Thickness -ArgumentList 8, 0, 8, 0
  $price.Foreground = $script:textBrush
  [System.Windows.Controls.Grid]::SetRow($price, 1)
  $grid.Children.Add($price) | Out-Null

  $chg = New-Object System.Windows.Controls.TextBlock
  $chg.FontSize = 11
  $chg.Margin = New-Object System.Windows.Thickness -ArgumentList 8, 0, 8, 0
  $chg.Foreground = $script:dimBrush
  [System.Windows.Controls.Grid]::SetRow($chg, 2)
  $grid.Children.Add($chg) | Out-Null

  $spark = New-Object System.Windows.Shapes.Polyline
  $spark.Width = 174
  $spark.Height = 30
  $spark.Margin = New-Object System.Windows.Thickness -ArgumentList 8, 0, 8, 2
  $spark.StrokeThickness = 1.4
  $spark.StrokeLineJoin = [System.Windows.Media.PenLineJoin]::Round
  $spark.HorizontalAlignment = 'Left'
  $spark.VerticalAlignment = 'Bottom'
  [System.Windows.Controls.Grid]::SetRow($spark, 3)
  $grid.Children.Add($spark) | Out-Null

  $border.Child = $grid
  $script:canvas.Children.Add($border) | Out-Null

  $card = [pscustomobject]@{
    Border = $border; Name = $name; Code = $code;
    Price = $price; Chg = $chg; Spark = $spark
  }

  $border.Add_MouseLeftButtonDown({
    param($sender, $e)
    $script:drag = @{
      Border = $sender
      StartX = $e.GetPosition($script:window).X
      StartY = $e.GetPosition($script:window).Y
      Left = [double][System.Windows.Controls.Canvas]::GetLeft($sender)
      Top = [double][System.Windows.Controls.Canvas]::GetTop($sender)
      Moved = $false
    }
    [void]$sender.CaptureMouse()
    $e.Handled = $true
  })
  $border.Add_MouseMove({
    param($sender, $e)
    if ($null -eq $script:drag -or $script:drag.Border -ne $sender) { return }
    if ($e.LeftButton -ne [System.Windows.Input.MouseButtonState]::Pressed) { return }
    $p = $e.GetPosition($script:window)
    $dx = $p.X - $script:drag.StartX
    $dy = $p.Y - $script:drag.StartY
    if ([Math]::Abs($dx) + [Math]::Abs($dy) -gt 4) { $script:drag.Moved = $true }
    $x = [Math]::Max(0, $script:drag.Left + $dx)
    $y = [Math]::Max(0, $script:drag.Top + $dy)
    [System.Windows.Controls.Canvas]::SetLeft($sender, $x)
    [System.Windows.Controls.Canvas]::SetTop($sender, $y)
  })
  $border.Add_MouseLeftButtonUp({
    param($sender, $e)
    if ($null -eq $script:drag -or $script:drag.Border -ne $sender) { return }
    $moved = $script:drag.Moved
    [void]$sender.ReleaseMouseCapture()
    $script:drag = $null
    if ($moved) {
      Save-Positions
    } else {
      $code = [string]$sender.Tag
      Start-Process "$WebBase/#/detail/$code"
    }
    $e.Handled = $true
  })
  return $card
}

function Update-Card($card, $item) {
  $card.Name.Text = [string]$item.name
  $card.Code.Text = [string]$item.code
  if ($null -ne $item.price) {
    $card.Price.Text = ([double]$item.price).ToString('F2')
  } else {
    $card.Price.Text = '—'
  }
  if ($null -ne $item.change_pct) {
    $p = [double]$item.change_pct
    $brush = if ($p -ge 0) { $script:upBrush } else { $script:downBrush }
    $card.Price.Foreground = $brush
    $card.Chg.Foreground = $brush
    $chg = if ($null -ne $item.change) { [double]$item.change } else { 0 }
    $card.Chg.Text = ('{0}{1:F2}  {2}{3:F2}%' -f $(if ($chg -ge 0) {'+'} else {''}), $chg,
      $(if ($p -ge 0) {'+'} else {''}), $p)
    $card.Spark.Stroke = $brush
  } else {
    $card.Price.Foreground = $script:textBrush
    $card.Chg.Foreground = $script:dimBrush
    $card.Chg.Text = '—'
    $card.Spark.Stroke = $script:dimBrush
  }

  $vals = @($item.spark | Where-Object { $null -ne $_ })
  if ($vals.Count -ge 2) {
    $min = ($vals | Measure-Object -Minimum).Minimum
    $max = ($vals | Measure-Object -Maximum).Maximum
    $span = $max - $min
    if ($span -le 0) { $span = 1 }
    $pc = New-Object System.Windows.Media.PointCollection
    for ($i = 0; $i -lt $vals.Count; $i++) {
      $x = ($i / ($vals.Count - 1)) * 174
      $y = 28 - (([double]$vals[$i] - $min) / $span) * 26
      $pc.Add((New-Object System.Windows.Point -ArgumentList ([double]$x), ([double]$y)))
    }
    $card.Spark.Points = $pc
  } else {
    $card.Spark.Points = New-Object System.Windows.Media.PointCollection
  }
}

function Set-Layout {
  $codes = @($script:cards.Keys | Sort-Object)
  $n = $codes.Count
  if ($n -eq 0) { return }
  $cols = [Math]::Ceiling([Math]::Sqrt($n))
  $rows = [Math]::Ceiling($n / $cols)
  $maxX = 0.0
  $maxY = 0.0
  for ($i = 0; $i -lt $n; $i++) {
    $code = $codes[$i]
    $card = $script:cards[$code]
    if ($script:positions.ContainsKey($code)) {
      $x = [double]$script:positions[$code].x
      $y = [double]$script:positions[$code].y
    } else {
      $x = 10 + ($i % $cols) * 200
      $y = 10 + [Math]::Floor($i / $cols) * 126
    }
    $x = [Math]::Max(0, $x)
    $y = [Math]::Max(0, $y)
    [System.Windows.Controls.Canvas]::SetLeft($card.Border, $x)
    [System.Windows.Controls.Canvas]::SetTop($card.Border, $y)
    if ($x + 190 -gt $maxX) { $maxX = $x + 190 }
    if ($y + 116 -gt $maxY) { $maxY = $y + 116 }
  }
  $w = [Math]::Max($cols * 190 + ($cols + 1) * 10, $maxX + 10)
  $h = [Math]::Max($rows * 116 + ($rows + 1) * 10, $maxY + 10)
  $script:window.Width = $w
  $script:window.Height = $h
  if (-not $script:placed) {
    $area = [System.Windows.SystemParameters]::WorkArea
    $script:window.Left = $area.Right - $w - 20
    $script:window.Top = $area.Top + 20
    $script:placed = $true
  }
}

function Reset-Positions {
  $script:positions = @{}
  Set-Layout
  Save-Positions
}

function Update-Data {
  $data = Get-WatchData
  $items = @($data.items)
  $codes = @($items | ForEach-Object { [string]$_.code })
  foreach ($code in @($script:cards.Keys)) {
    if ($codes -notcontains $code) {
      $script:canvas.Children.Remove($script:cards[$code].Border)
      $script:cards.Remove($code)
    }
  }
  foreach ($item in $items) {
    $code = [string]$item.code
    if (-not $script:cards.ContainsKey($code)) {
      $script:cards[$code] = New-Card $item
    }
    Update-Card $script:cards[$code] $item
  }
  if ($null -eq $script:drag) { Set-Layout }
}

# ── XAML ──
$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="K道浮窗" Width="400" Height="126"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        Topmost="True" ShowInTaskbar="False" ResizeMode="NoResize"
        WindowStartupLocation="Manual" SnapsToDevicePixels="True">
  <Canvas x:Name="RootCanvas" Background="Transparent" />
</Window>
'@
$doc = New-Object System.Xml.XmlDocument
$doc.LoadXml($xaml)
$window = [System.Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $doc))
$canvas = $window.FindName('RootCanvas')
$script:window = $window
$script:canvas = $canvas
Load-Positions

# 右键菜单：整理 / 退出；Esc 退出
$menu = New-Object System.Windows.Controls.ContextMenu
$miReset = New-Object System.Windows.Controls.MenuItem
$miReset.Header = '整理布局'
$miReset.Add_Click({ Reset-Positions })
$miExit = New-Object System.Windows.Controls.MenuItem
$miExit.Header = '退出'
$miExit.Add_Click({ $script:window.Close() })
$menu.Items.Add($miReset) | Out-Null
$menu.Items.Add($miExit) | Out-Null
$window.ContextMenu = $menu
$window.Add_KeyDown({ param($s, $e) if ($e.Key -eq 'Escape') { $script:window.Close() } })
$canvas.Add_MouseLeftButtonDown({
  param($s, $e)
  if ($e.ClickCount -eq 2) { Reset-Positions; $e.Handled = $true }
  elseif ($e.ClickCount -eq 1) { try { $script:window.DragMove() } catch { } }
})

try {
  Update-Data
} catch {
  if ($SelfTest) { exit 1 }
  [System.Windows.MessageBox]::Show("加载盯盘数据失败：`n$($_.Exception.Message)", 'K道浮窗') | Out-Null
  exit 1
}

if ($SelfTest) {
  $codes = @($script:cards.Keys | Sort-Object)
  Write-Output ('OK cards={0} window={1}x{2}' -f $codes.Count, $window.Width, $window.Height)
  foreach ($code in $codes) {
    $c = $script:cards[$code]
    Write-Output ('CARD {0} {1} {2}' -f $code, $c.Price.Text, $c.Chg.Text)
  }
  exit 0
}

$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds(5)
$timer.Add_Tick({
  try { Update-Data } catch { }
})
$timer.Start()
if ($RunSeconds -gt 0) {
  $autoTimer = New-Object System.Windows.Threading.DispatcherTimer
  $autoTimer.Interval = [TimeSpan]::FromSeconds($RunSeconds)
  $autoTimer.Add_Tick({ $script:window.Close() })
  $autoTimer.Start()
}
$window.ShowDialog() | Out-Null
$timer.Stop()
