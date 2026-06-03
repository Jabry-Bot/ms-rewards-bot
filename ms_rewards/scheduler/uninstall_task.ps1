$TaskName = "MsRewardsBot"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarea '$TaskName' eliminada."
} else {
    Write-Host "Tarea '$TaskName' no existe - nada que hacer."
}
