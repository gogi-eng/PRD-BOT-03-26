# Установка автозапуска и ярлыка на рабочий стол
# Имя: АГЕНТ Дубовика (№ 007)
$ErrorActionPreference = "Stop"
$agentDir = "C:\Users\v.dubovik\DocAgent"
$bat = Join-Path $agentDir "start_agent.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$startup = [Environment]::GetFolderPath("Startup")
$agentName = "АГЕНТ Дубовика (№ 007)"

$WshShell = New-Object -ComObject WScript.Shell

# Удалить старые ярлыки «Делопроизводитель», если есть
foreach ($old in @(
    (Join-Path $desktop "Делопроизводитель.lnk"),
    (Join-Path $startup "Делопроизводитель.lnk")
)) {
    if (Test-Path $old) { Remove-Item $old -Force }
}

# Ярлык на рабочий стол
$deskLnk = Join-Path $desktop "$agentName.lnk"
$sc = $WshShell.CreateShortcut($deskLnk)
$sc.TargetPath = $bat
$sc.WorkingDirectory = $agentDir
$sc.WindowStyle = 7
$sc.Description = $agentName
$sc.IconLocation = (Join-Path $agentDir "avatar\mascot.ico")
$sc.Save()

# Автозапуск при входе в Windows
$startLnk = Join-Path $startup "$agentName.lnk"
$sc2 = $WshShell.CreateShortcut($startLnk)
$sc2.TargetPath = $bat
$sc2.WorkingDirectory = $agentDir
$sc2.WindowStyle = 7
$sc2.Description = "Автозапуск: $agentName"
$sc2.IconLocation = (Join-Path $agentDir "avatar\mascot.ico")
$sc2.Save()

Write-Host "OK: ярлык на рабочем столе: $deskLnk"
Write-Host "OK: автозапуск: $startLnk"

# Ярлык быстрого исправления по правилам СНиОТ
$fixBat = Join-Path $agentDir "Исправить_ДИ_САТП.bat"
$fixLnk = Join-Path $desktop "Исправить СНиОТ.lnk"
$sc3 = $WshShell.CreateShortcut($fixLnk)
$sc3.TargetPath = $fixBat
$sc3.WorkingDirectory = $agentDir
$sc3.WindowStyle = 7
$sc3.Description = "Правила оформления документов СНиОТ (ДИ, РИ, положения…)"
$sc3.IconLocation = (Join-Path $agentDir "avatar\mascot.ico")
$sc3.Save()
Write-Host "OK: ярлык правил СНиОТ: $fixLnk"
# Удалить старый ярлык с узким названием
$oldFix = Join-Path $desktop "Исправить ДИ САТП.lnk"
if (Test-Path $oldFix) { Remove-Item $oldFix -Force }
