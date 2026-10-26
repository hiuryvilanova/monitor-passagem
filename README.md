# Buscador de passagens + hospedagem

Script pessoal que busca os voos mais baratos no **Google Flights** (que já reúne LATAM, GOL, Azul e agências) e a hospedagem mais barata no **Google Hotels** (Booking, Expedia, Hoteis.com, site do hotel…), testando datas flexíveis e montando o melhor pacote.

## Instalação (uma vez)

```bash
pip install -r requirements.txt
```

Hospedagem (opcional): crie uma conta grátis em https://serpapi.com, copie `.env.example` para `.env` e cole a chave em `SERPAPI_KEY=`.

## Uso

```bash
python passagem.py --origem BSB --destino THE --ida 2026-11-20 --volta 2026-11-25 --flex 2
```

| Opção | O que faz |
|---|---|
| `--origem` / `--destino` | Código do aeroporto (BSB, THE, GRU, REC…) |
| `--ida` / `--volta` | Datas AAAA-MM-DD. Sem `--volta` = só ida |
| `--flex N` | Testa N dias antes e depois de cada data |
| `--adultos`, `--criancas` | Passageiros (padrão 1 adulto) |
| `--max-paradas 0` | Só voo direto |
| `--malas 1` | Inclui 1 mala despachada no preço |
| `--cidade-hotel` | Cidade da hospedagem (padrão: cidade do destino) |
| `--nota-min 4.0` | Só hotéis com nota 3.5 / 4.0 / 4.5+ |
| `--sem-hotel` | Pula a hospedagem |
| `--alerta 3000` | Avisa se ida + volta mais baratas custarem até R$ 3.000 |
| `--email` | Manda o alerta por e-mail (precisa do `.env` configurado) |

## Painel online 24h (GitHub Actions + Pages)

`painel.py` busca os voos de `monitor.json`, guarda o histórico em `dados/historico.json` e gera `docs/index.html`:
uma coluna por dia com companhia, horários, preço e etiquetas (Mais barato, Muito caro, Em cima da hora, Madrugada…).
O workflow `.github/workflows/monitor.yml` roda isso **toda hora** no GitHub e publica o painel no GitHub Pages.

- Mudar rota, datas, prazo de chegada, preço de referência ou limite: edite `monitor.json` no GitHub.
- Rodar na hora: aba **Actions** → *Monitor de passagens* → **Run workflow**.
- E-mail: em **Settings → Secrets and variables → Actions**, crie `EMAIL_REMETENTE`, `EMAIL_SENHA_APP` e `EMAIL_PARA`.

## Monitoramento local com alerta por e-mail

O script também avisa sempre que o preço **cair** em relação à verificação anterior (histórico em `resultados/historico.json`).

1. Crie uma senha de app do Gmail em https://myaccount.google.com/apppasswords (exige verificação em duas etapas) e cole em `EMAIL_SENHA_APP=` no `.env`.
2. Ajuste datas e limite em `monitorar.ps1`.
3. Agende para rodar a cada 3 horas (uma vez só):

```powershell
powershell -ExecutionPolicy Bypass -File agendar.ps1
```

O log de cada execução fica em `resultados/monitor.log`. O computador precisa estar ligado. Para parar:

```powershell
Unregister-ScheduledTask -TaskName "Monitor Passagem BSB-THE" -Confirm:$false
```

Saída: resumo no terminal + `resultados/<rota>_<data>.html` (relatório com links) + `resultados/<rota>_<data>_voos.csv` (abre no Excel).

## Observações

- Ida e volta são buscadas como trechos separados (só ida), o que permite misturar companhias — geralmente sai mais barato.
- O preço do hotel no pacote é estimado pelo valor por noite do mais barato nas datas `--ida`/`--volta`.
- Cada busca de voo leva ~2 s. `--flex 3` com ida e volta = 14 buscas. Não exagere para não ser bloqueado pelo Google.
- Preços mudam o tempo todo: confira no link antes de comprar.

## Bibliotecas usadas

- [AWeirdDev/flights (`fast-flights`)](https://github.com/AWeirdDev/flights) — Google Flights, sem chave.
- [SerpApi Google Hotels API](https://serpapi.com/google-hotels-api) — hospedagem, plano grátis.
