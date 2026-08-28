# Включить ежедневный отчёт СНиОТ: 16:30 (сегодня) и 08:30 (догон вчерашнего).
$ErrorActionPreference = "Stop"

$py = "C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe"
if (-not (Test-Path $py)) {
    $py = (Get-Command python -ErrorAction Stop).Source
}
$work = "C:\Users\v.dubovik\Desktop\Еженедельный_итог"
$script = Join-Path $work "weekly_report.py"

if (-not (Test-Path $script)) {
    throw "Не найден скрипт: $script"
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

function Register-SniotDailyTask {
    param(
        [string]$Name,
        [string]$Arguments,
        [datetime]$At,
        [string]$Description
    )
    $action = New-ScheduledTaskAction -Execute $py -Argument $Arguments -WorkingDirectory $work
    $trigger = New-ScheduledTaskTrigger -Daily -At $At
    Register-ScheduledTask `
        -TaskName $Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force `
        -Description $Description | Out-Null
}

$taskDay = "СНиОТ_ежедневный_отчёт"
$taskCatch = "СНиОТ_ежедневный_отчёт_догон"

Register-SniotDailyTask `
    -Name $taskDay `
    -Arguments "`"$script`" --daily" `
    -At ([datetime]"16:30") `
    -Description "Ежедневный отчёт СНиОТ за СЕГОДНЯ. 16:30 Москва. Если ПК выключен — файл за этот день не создаётся (догон утром 08:30)."

Register-SniotDailyTask `
    -Name $taskCatch `
    -Arguments "`"$script`" --daily --catch-up" `
    -At ([datetime]"08:30") `
    -Description "Догон: если вчера в 16:30 ПК был выключен и файла нет — отчёт ЗА ВЧЕРА в 08:30. Если файл уже есть — ничего не делает."

Write-Host ""
Write-Host "Готово. Включены две задачи:"
Write-Host "  1) $taskDay — каждый день в 16:30, отчёт за сегодня"
Write-Host "  2) $taskCatch — каждый день в 08:30, догон за вчера (только если файла нет)"
Write-Host "Куда: Рабочий стол \ Ежедневные отчёты \ на_правку"
Write-Host "Файл откроется в Word, если вы вошли в Windows. Догон при уже готовом файле Word не открывает."
Write-Host ""
Write-Host "Проверить:"
Write-Host "  schtasks /Query /TN `"$taskDay`" /FO LIST"
Write-Host "  schtasks /Query /TN `"$taskCatch`" /FO LIST"
Write-Host "Выключить обе:"
Write-Host "  schtasks /Delete /TN `"$taskDay`" /F"
Write-Host "  schtasks /Delete /TN `"$taskCatch`" /F"
Write-Host ""
Write-Host "Если выключены И 16:30, И утро 08:30 — отчёт сам не появится."
Write-Host "Тогда запустите «Сделать ежедневный отчёт.bat» (сегодня) или:"
Write-Host "  python weekly_report.py --daily --catch-up"
