# Registra la Scheduled Task "MsRewardsBot" para el usuario actual.
# Idempotente: si ya existe la tarea, la sobrescribe.

$ErrorActionPreference = "Stop"
$TaskName = "MsRewardsBot"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir   = Split-Path -Parent $ScriptDir
$VenvPyW   = Join-Path $RepoDir ".venv\Scripts\pythonw.exe"
$VenvPy    = Join-Path $RepoDir ".venv\Scripts\python.exe"
$RunScript = Join-Path $RepoDir "run.py"

if (-not (Test-Path $RunScript)) {
    Write-Error "No encuentro $RunScript"
    exit 1
}

if (Test-Path $VenvPyW) {
    $Executable = $VenvPyW
} elseif (Test-Path $VenvPy) {
    $Executable = $VenvPy
} else {
    $Executable = "pythonw.exe"
}

$RandHour  = Get-Random -Minimum 10 -Maximum 14
$RandMin   = Get-Random -Minimum 0 -Maximum 60
$DailyTime = (Get-Date -Hour $RandHour -Minute $RandMin -Second 0).ToString("HH:mm:ss")

Write-Host "Registrando '$TaskName' para el usuario actual..."
Write-Host "  Executable      : $Executable"
Write-Host "  Script          : $RunScript"
Write-Host "  WorkingDirectory: $RepoDir"
Write-Host "  Daily trigger   : $DailyTime"

$Action = New-ScheduledTaskAction -Execute $Executable -Argument "`"$RunScript`"" -WorkingDirectory $RepoDir

$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$DelayMin = Get-Random -Minimum 2 -Maximum 11
$LogonTrigger.Delay = "PT" + $DelayMin + "M"

$DailyTrigger = New-ScheduledTaskTrigger -Daily -At $DailyTime

$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "La tarea ya existe - la voy a sobrescribir."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($LogonTrigger, $DailyTrigger) -Settings $Settings -Principal $Principal -Description "Microsoft Rewards bot (auto-run diario)" | Out-Null

Write-Host "Listo. Comprueba con: schtasks /Query /TN $TaskName /V"
