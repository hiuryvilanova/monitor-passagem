# Roda a busca de voos e manda e-mail se o preço cair ou ficar abaixo do limite.
# Edite as datas e o limite abaixo. O Agendador de Tarefas do Windows chama este arquivo.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

python passagem.py --origem BSB --destino THE --ida 2026-10-29 --volta 2026-11-07 --flex 2 `
    --sem-hotel --alerta 3000 --email *>> "resultados\monitor.log"
