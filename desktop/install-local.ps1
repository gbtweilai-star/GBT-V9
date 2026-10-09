# 安装 GBT小土豆V9 的快捷方式与“可卸载”登记（HKCU，无需管理员）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 说明：本机 NSIS 静默安装返回 rc=2，故采用“解压式安装 + 快捷方式 + 可卸载登记”的等效做法。
# 版本号只有一个来源：本目录 package.json（避免登记里写死旧版本）
$ErrorActionPreference = 'Stop'

$pkgJson = Join-Path $PSScriptRoot 'package.json'
$version = '0.0.0'
if (Test-Path $pkgJson) {
  try { $version = (Get-Content $pkgJson -Raw -Encoding UTF8 | ConvertFrom-Json).version } catch { }
}

$install = Join-Path $env:LOCALAPPDATA 'Programs\GBT小土豆V9'
$exe     = Join-Path $install 'GBT小土豆V9.exe'
if (-not (Test-Path $exe)) { throw "安装目录缺少主程序: $exe" }

# 图标（若打包里有 icon.ico 就用它，否则用 exe 自带）
$icon = Join-Path $install 'resources\icon.ico'
$iconRef = if (Test-Path $icon) { $icon } else { $exe }

# ── 1) 卸载脚本：删目录 + 删快捷方式 + 删注册项（本机数据 %APPDATA%\GBT小土豆V9 默认保留）──
$uninst = Join-Path $install 'uninstall.cmd'
$uninstBody = @"
@echo off
chcp 65001 >nul
echo 正在卸载 GBT小土豆V9 ...
taskkill /IM "GBT小土豆V9.exe" /T /F >nul 2>nul
timeout /t 2 >nul
rd /s /q "%LOCALAPPDATA%\Programs\GBT小土豆V9"
del "%USERPROFILE%\Desktop\GBT小土豆V9.lnk" >nul 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\GBT小土豆V9.lnk" >nul 2>nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\GBT小土豆V9" /f >nul 2>nul
echo 已卸载（用户数据 %APPDATA%\GBT小土豆V9 保留；如需清除请手动删除）
pause
"@
Set-Content -Path $uninst -Value $uninstBody -Encoding UTF8

# ── 2) 快捷方式：桌面 + 开始菜单 ──
$ws = New-Object -ComObject WScript.Shell
$targets = @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'GBT小土豆V9.lnk'),
  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\GBT小土豆V9.lnk')
)
foreach ($lnkPath in $targets) {
  $sc = $ws.CreateShortcut($lnkPath)
  $sc.TargetPath       = $exe
  $sc.WorkingDirectory = $install
  $sc.IconLocation     = "$iconRef,0"
  $sc.Description      = 'GBT小土豆V9 · 万物皆可插 · 万物皆可控'
  $sc.Save()
  Write-Host "shortcut -> $lnkPath"
}

# ── 3) 卸载登记（HKCU，列出 0.12.0）──
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GBT小土豆V9'
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name 'DisplayName'     -Value 'GBT小土豆V9'
Set-ItemProperty -Path $key -Name 'DisplayVersion'  -Value $version
Set-ItemProperty -Path $key -Name 'Publisher'       -Value 'GBT小土豆V9'
Set-ItemProperty -Path $key -Name 'InstallLocation' -Value $install
Set-ItemProperty -Path $key -Name 'DisplayIcon'     -Value "$iconRef,0"
Set-ItemProperty -Path $key -Name 'UninstallString' -Value "`"$uninst`""
Set-ItemProperty -Path $key -Name 'NoModify'        -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name 'NoRepair'        -Value 1 -Type DWord
Write-Host "uninstall entry -> $key"
Write-Host "OK"
