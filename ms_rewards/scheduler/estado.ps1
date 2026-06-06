# Muestra el estado del bot: tarea programada + ultima corrida (last_run.json).
$ErrorActionPreference = 'SilentlyContinue'

$TaskName = 'MsRewardsBot'
$LastRun  = Join-Path $PSScriptRoot '..\state\last_run.json'

Write-Host '=== Tarea programada (MsRewardsBot) ==='
Write-Host ''
$task = Get-ScheduledTask -TaskName $TaskName
if ($null -eq $task) {
    Write-Host '  [X] NO registrada en el programador de tareas.'
    Write-Host '      El bot no se ejecutara solo. Ejecuta setup.bat.'
} else {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host ("  [OK] Registrada    Estado: {0}" -f $task.State)
    Write-Host ("       Ultima vez : {0}  (codigo: {1})" -f $info.LastRunTime, $info.LastTaskResult)
    Write-Host ("       Proxima vez: {0}" -f $info.NextRunTime)
}

Write-Host ''
Write-Host '=== Ultima corrida (last_run.json) ==='
Write-Host ''
if (Test-Path $LastRun) {
    Get-Content $LastRun -Raw | Write-Host
} else {
    Write-Host '  No existe last_run.json - el bot todavia no ha completado ninguna corrida.'
    Write-Host ("  Ruta esperada: {0}" -f (Resolve-Path $LastRun -ErrorAction SilentlyContinue))
}
