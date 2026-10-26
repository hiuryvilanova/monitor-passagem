# Cria (ou recria) a tarefa do Windows que roda monitorar.ps1 toda hora cheia.
# Para remover depois: Unregister-ScheduledTask -TaskName "Monitor Passagem BSB-THE" -Confirm:$false
$nome = "Monitor Passagem BSB-THE"
$script = Join-Path $PSScriptRoot "monitorar.ps1"

$acao = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" -WorkingDirectory $PSScriptRoot
$proximaHora = (Get-Date).Date.AddHours((Get-Date).Hour + 1)
$gatilho = New-ScheduledTaskTrigger -Once -At $proximaHora -RepetitionInterval (New-TimeSpan -Hours 1)
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $config -Force `
    -Description "Busca passagens toda hora e publica o painel no GitHub" | Out-Null
Get-ScheduledTask -TaskName $nome | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
