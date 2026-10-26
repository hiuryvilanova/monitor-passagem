# Cria (ou recria) a tarefa do Windows que roda monitorar.ps1 a cada 3 horas.
# Para remover depois: Unregister-ScheduledTask -TaskName "Monitor Passagem BSB-THE" -Confirm:$false
$nome = "Monitor Passagem BSB-THE"
$script = Join-Path $PSScriptRoot "monitorar.ps1"

$acao = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" -WorkingDirectory $PSScriptRoot
$gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 3)
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $config -Force `
    -Description "Verifica preço de passagens a cada 3h e manda e-mail" | Out-Null
Get-ScheduledTask -TaskName $nome | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
