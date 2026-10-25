# Установка Говорилки на Windows одной командой (PowerShell):
#
#   irm https://raw.githubusercontent.com/fedintsevqq/govorilka/main/install.ps1 | iex
#
# Скрипт берёт последний релиз с GitHub, сверяет SHA-256 файла с тем, что опубликовал GitHub, кладёт программу
# в %LOCALAPPDATA%\Programs\Govorilka, создаёт ярлык в «Пуске» и запускает её. Файл, скачанный так, не помечается
# как «из интернета», поэтому SmartScreen не предупреждает о неизвестном издателе. Прав администратора не нужно.

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'   # без полосы прогресса загрузка в Windows PowerShell идёт в разы быстрее

$repo = 'fedintsevqq/govorilka'
Write-Host 'Говорилка: ищу последнюю версию...'
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'Govorilka-installer' }
$asset = $release.assets | Where-Object { $_.name -like '*-windows-x64.exe' } | Select-Object -First 1
if (-not $asset) { throw "В релизе $($release.tag_name) нет файла для Windows" }
if (-not $asset.digest -or -not $asset.digest.StartsWith('sha256:')) { throw 'GitHub не опубликовал контрольную сумму файла: скачайте его вручную со страницы релиза' }
$expected = $asset.digest.Substring(7).ToLower()

# GOVORILKA_INSTALL_DIR и GOVORILKA_NO_START — для проверки скрипта в CI: своя папка, без ярлыка и запуска
$dir = if ($env:GOVORILKA_INSTALL_DIR) { $env:GOVORILKA_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'Programs\Govorilka' }
$quiet = [bool]$env:GOVORILKA_NO_START
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$target = Join-Path $dir 'Govorilka.exe'
$download = Join-Path $dir 'Govorilka.download'

Write-Host "Скачиваю $($asset.name) ($([math]::Round($asset.size / 1MB)) МБ)..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $download -UseBasicParsing -Headers @{ 'User-Agent' = 'Govorilka-installer' }
$actual = (Get-FileHash -Algorithm SHA256 -Path $download).Hash.ToLower()
if ($actual -ne $expected) {
    Remove-Item -Force $download
    throw 'Контрольная сумма не совпала: файл повреждён или подменён. Попробуйте ещё раз'
}

Get-Process -Name 'Govorilka' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $target } | Stop-Process -Force
Start-Sleep -Milliseconds 500
Move-Item -Force -Path $download -Destination $target

if ($quiet) { Write-Host "Готово: $target"; return }

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'Говорилка.lnk'))
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $dir
$shortcut.Description = 'Говорилка: расшифровка созвонов'
$shortcut.Save()

Write-Host "Готово: Говорилка $($release.tag_name) установлена в $dir. Ярлык — в меню «Пуск». Запускаю..."
Start-Process -FilePath $target
