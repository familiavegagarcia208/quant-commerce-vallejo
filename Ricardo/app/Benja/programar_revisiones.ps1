# Programa la revisión de existencias dos veces al día en el Programador de tareas de Windows.
# Uso (desde la carpeta del proyecto):
#   powershell -ExecutionPolicy Bypass -File programar_revisiones.ps1
#   powershell -ExecutionPolicy Bypass -File programar_revisiones.ps1 -HoraManana 08:30 -HoraTarde 17:00
#   powershell -ExecutionPolicy Bypass -File programar_revisiones.ps1 -Quitar
param(
    [string]$HoraManana = "09:00",
    [string]$HoraTarde = "16:00",
    [string]$Python = "",
    [switch]$Quitar
)

$proyecto = $PSScriptRoot
$tareas = @{ "PriceShoes_Existencias_Manana" = $HoraManana; "PriceShoes_Existencias_Tarde" = $HoraTarde }

if ($Quitar) {
    foreach ($nombre in $tareas.Keys) {
        Unregister-ScheduledTask -TaskName $nombre -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Tarea eliminada: $nombre"
    }
    return
}

if (-not $Python) {
    $venv = Join-Path $proyecto "venv\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { (Get-Command python).Source }
}

$registro = Join-Path $proyecto "data\revision_existencias.log"
$comando = "`"$Python`" revisar_existencias.py >> `"$registro`" 2>&1"

foreach ($nombre in $tareas.Keys) {
    $accion = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $comando" -WorkingDirectory $proyecto
    $disparador = New-ScheduledTaskTrigger -Daily -At $tareas[$nombre]
    $ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName $nombre -Action $accion -Trigger $disparador -Settings $ajustes -Force | Out-Null
    Write-Host "Tarea programada: $nombre a las $($tareas[$nombre])"
}
Write-Host "Python: $Python"
Write-Host "Registro: $registro"
