# Busca no seu computador e publica o painel no GitHub. O Agendador do Windows roda isto toda hora.
# Se o computador estiver desligado, o GitHub faz a busca sozinho (veja .github/workflows/monitor.yml).
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$env:ORIGEM_BUSCA = "pc"
$log = "resultados\monitor.log"
New-Item -ItemType Directory -Force resultados | Out-Null

function Registrar($texto) { "$(Get-Date -Format 'dd/MM HH:mm') $texto" | Out-File -Append -Encoding utf8 $log }

Registrar "--- início"
# Traz o que o GitHub atualizou enquanto o computador estava desligado (em conflito, fica a versão local).
git pull --rebase -X theirs -q *>> $log
python painel.py *>> $log

git add docs dados
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -q -m "Atualiza preços (PC) $(Get-Date -Format 'dd/MM HH:mm')" *>> $log
    git push -q *>> $log
    if ($LASTEXITCODE -ne 0) {
        git pull --rebase -X theirs -q *>> $log
        git push -q *>> $log
    }
    if ($LASTEXITCODE -eq 0) { Registrar "painel enviado ao GitHub" } else { Registrar "FALHA ao enviar ao GitHub" }
}
