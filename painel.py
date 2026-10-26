"""Monitor 24h: busca todas as datas do período em monitor.json, monta as combinações
ida + volta (estadia entre dias_min e dias_max), guarda o histórico e gera o painel
docs/index.html (dia a dia, com horários, companhias, faixas de preço e etiquetas).

Uso:  python painel.py            (roda uma verificação)
"""

import html
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import passagem as p

PASTA = Path(__file__).parent
HISTORICO = PASTA / "dados" / "historico.json"
PAINEL = PASTA / "docs" / "index.html"
BRT = timezone(timedelta(hours=-3))
DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
CORES_CIA = {"gol": "#ff6a00", "latam": "#e8114b", "azul": "#1f6fd1"}


def chave(v: p.Voo) -> str:
    return f"{v.data}|{v.companhias}|{v.saida}|{v.trecho}"


def dia_curto(d: date) -> str:
    return f"{DIAS_SEMANA[d.weekday()]} {d:%d/%m}"


def sem_repetidos(voos: list[p.Voo]) -> list[p.Voo]:
    unicos: dict[str, p.Voo] = {}
    for v in voos:
        if chave(v) not in unicos or v.preco < unicos[chave(v)].preco:
            unicos[chave(v)] = v
    return sorted(unicos.values(), key=lambda v: v.preco)


def periodo(cfg: dict, hoje: date) -> tuple[list[date], list[date]]:
    """Dias de ida e de volta possíveis dentro do período e da estadia mínima."""
    ida_de = cfg.get("ida_de", "hoje")
    inicio = hoje if ida_de == "hoje" else max(hoje, date.fromisoformat(ida_de))
    fim = date.fromisoformat(cfg["volta_ate"])
    dmin = cfg.get("dias_min", 1)
    n = (fim - inicio).days - dmin + 1
    idas = [inicio + timedelta(days=d) for d in range(max(n, 0))]
    voltas = [inicio + timedelta(days=dmin + d) for d in range(max(n, 0))]
    return idas, voltas


def buscar(cfg: dict, hoje: date) -> dict[str, list[p.Voo]]:
    args = SimpleNamespace(adultos=cfg.get("adultos", 1), criancas=cfg.get("criancas", 0),
                           max_paradas=cfg.get("max_paradas"), malas=cfg.get("malas", 0))
    idas, voltas = periodo(cfg, hoje)
    trechos = {"ida": (cfg["origem"], cfg["destino"], idas),
               "volta": (cfg["destino"], cfg["origem"], voltas)}
    resultado = {}
    for sentido, (de, para, dias) in trechos.items():
        voos = []
        for dia in dias:
            print(f"✈  {sentido:<5} {de}->{para} em {dia:%d/%m}...")
            voos += p.buscar_voos(de, para, dia, args)
            time.sleep(p.PAUSA_ENTRE_BUSCAS)
        resultado[sentido] = sem_repetidos(voos)
    return resultado


def carregar_historico() -> dict:
    if HISTORICO.exists():
        return json.loads(HISTORICO.read_text(encoding="utf-8"))
    return {"execucoes": [], "precos": {}}


def salvar_historico(hist: dict) -> None:
    HISTORICO.parent.mkdir(exist_ok=True)
    hist["execucoes"] = hist["execucoes"][-24 * 30:]  # guarda 30 dias de verificações
    HISTORICO.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")


def combinacoes(voos: dict, cfg: dict, prazo: date) -> list[dict]:
    """Melhor ida de cada dia x melhor volta de cada dia, com estadia entre dias_min e dias_max."""
    ida_dia: dict[date, p.Voo] = {}
    for v in voos["ida"]:
        ida_dia.setdefault(v.data, v)
    volta_dia: dict[date, p.Voo] = {}
    for v in voos["volta"]:
        if v.chega_em <= prazo:
            volta_dia.setdefault(v.data, v)
    dmin, dmax = cfg.get("dias_min", 1), cfg.get("dias_max", 30)
    combos = [{"total": i.preco + v.preco, "ida": i, "volta": v, "dias": (v.data - i.data).days}
              for i in ida_dia.values() for v in volta_dia.values()
              if dmin <= (v.data - i.data).days <= dmax]
    return sorted(combos, key=lambda c: c["total"])


def faixa(preco: int, faixas: list[int], mult: int = 1) -> tuple[str, str]:
    """(classe, texto) da faixa de preço. mult=2 para ida + volta."""
    f = [x * mult for x in faixas]
    if preco <= f[0]:
        return "promo", f"Até {p.reais(f[0])}"
    if preco <= f[1]:
        return "bom", f"Até {p.reais(f[1])}"
    if preco <= f[2]:
        return "ok", f"Até {p.reais(f[2])}"
    return "ruim", f"Acima de {p.reais(f[2])}"


def etiquetas(v: p.Voo, sentido: str, ctx: dict) -> list[tuple[str, str]]:
    """Lista de (texto, tipo). Tipos: promo, bom, ok, ruim, aviso, info."""
    tags = []
    if v is ctx["mais_barato"][sentido]:
        tags.append(("Mais barato", "bom"))
    elif v is ctx["melhor_do_dia"][(sentido, v.data)]:
        tags.append(("Melhor do dia", "info"))

    classe, texto = faixa(v.preco, ctx["faixas"])
    tags.append((texto, classe))
    mediana = ctx["mediana"][sentido]
    if v.preco <= mediana * 0.65:
        tags.append(("Fora do padrão: muito abaixo", "promo"))
    elif v.preco >= mediana * 1.5:
        tags.append(("Fora do padrão: muito acima", "ruim"))

    antes = ctx["anteriores"].get(chave(v))
    if antes and v.preco < antes:
        tags.append((f"↓ caiu {p.reais(antes - v.preco)}", "bom"))
    elif antes and v.preco > antes:
        tags.append((f"↑ subiu {p.reais(v.preco - antes)}", "ruim"))

    dias = (v.data - ctx["hoje"]).days
    if dias <= 2:
        tags.append(("Em cima da hora", "ruim"))
    elif dias <= 7:
        tags.append(("Menos de 1 semana", "aviso"))

    tags.append(("Direto", "bom") if v.paradas == 0 else
                (f"{v.paradas} conexão" if v.paradas == 1 else f"{v.paradas} conexões", "info"))
    if int(v.saida[:2]) < 6:
        tags.append(("Madrugada", "aviso"))
    if v.chega_em > v.data:
        tags.append(("Chega no dia seguinte", "aviso"))
    if sentido == "volta" and v.chega_em > ctx["prazo"]:
        tags.append((f"Chega após {ctx['prazo']:%d/%m}", "ruim"))
    return tags


def tempo_total(v: p.Voo) -> str:
    saida = datetime.combine(v.data, datetime.strptime(v.saida, "%H:%M").time())
    chegada = datetime.combine(v.chega_em, datetime.strptime(v.chegada, "%H:%M").time())
    minutos = int((chegada - saida).total_seconds() // 60)
    return f"{minutos // 60}h{minutos % 60:02d}"


def grafico(execucoes: list[dict]) -> str:
    pontos = [e for e in execucoes if e.get("melhor_total")][-168:]
    if len(pontos) < 2:
        return "<p class='sub'>O gráfico aparece a partir da segunda verificação.</p>"
    valores = [e["melhor_total"] for e in pontos]
    lo, hi = min(valores), max(valores)
    faixa_y = (hi - lo) or 1
    w, h = 600, 120
    xy = [(i * w / (len(valores) - 1), h - 10 - (val - lo) * (h - 20) / faixa_y) for i, val in enumerate(valores)]
    linha = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    inicio = datetime.fromisoformat(pontos[0]["quando"])
    return (f"<svg viewBox='0 0 {w} {h}' class='graf' preserveAspectRatio='none' role='img' "
            f"aria-label='Menor preço ida+volta ao longo do tempo'><polyline points='{linha}' /></svg>"
            f"<div class='graf-leg'><span>{inicio:%d/%m %H:%M}</span><span>mín {p.reais(lo)} · máx {p.reais(hi)}</span>"
            f"<span>agora {p.reais(valores[-1])}</span></div>")


def cia_html(companhias: str) -> str:
    cor = CORES_CIA.get(companhias.split(",")[0].strip().lower(), "#6b7280")
    return f"<span class='cia' style='--cia:{cor}'>{html.escape(companhias)}</span>"


def cartao_voo(v: p.Voo, sentido: str, ctx: dict) -> str:
    tags = etiquetas(v, sentido, ctx)
    classes = " ".join(["voo", "direto" if v.paradas == 0 else "",
                        "fora-prazo" if any(t.startswith("Chega após") for t, _ in tags) else ""])
    chips = "".join(f"<span class='tag {tipo}'>{html.escape(t)}</span>" for t, tipo in tags)
    mais_um = "<sup>+1</sup>" if v.chega_em > v.data else ""
    return (f"<a class='{classes}' href='{html.escape(v.link)}' target='_blank' rel='noopener'>"
            f"<div class='linha1'>{cia_html(v.companhias)}"
            f"<span class='preco n-{faixa(v.preco, ctx['faixas'])[0]}'>{p.reais(v.preco)}</span></div>"
            f"<div class='horas'>{v.saida} → {v.chegada}{mais_um}<span class='dur'>{tempo_total(v)}</span></div>"
            f"<div class='rota'>{html.escape(v.trecho)}</div><div class='tags'>{chips}</div></a>")


def secao(titulo: str, sentido: str, voos: list[p.Voo], ctx: dict) -> str:
    por_dia: dict[date, list[p.Voo]] = {}
    for v in voos:
        por_dia.setdefault(v.data, []).append(v)
    colunas = []
    for dia in sorted(por_dia):
        lista = por_dia[dia]
        menor = lista[0].preco
        faltam = (dia - ctx["hoje"]).days
        quando = "hoje" if faltam == 0 else "amanhã" if faltam == 1 else f"em {faltam} dias"
        colunas.append(
            f"<div class='dia'><div class='dia-cab n-{faixa(menor, ctx['faixas'])[0]}'>"
            f"<b>{dia_curto(dia)}</b><span>{quando}</span>"
            f"<span class='dia-preco'>a partir de {p.reais(menor)}</span></div>"
            + "".join(cartao_voo(v, sentido, ctx) for v in lista[:8]) + "</div>")
    if not colunas:
        colunas.append("<p class='sub'>Nenhum voo encontrado nesta verificação.</p>")
    return f"<h2>{titulo}</h2><div class='dias'>{''.join(colunas)}</div>"


def tabela_combinacoes(combos: list[dict], ctx: dict) -> str:
    """Grade ida (linhas) x volta (colunas) com o menor total de cada par."""
    if not combos:
        return "<p class='sub'>Nenhuma combinação dentro do período.</p>"
    por_par = {(c["ida"].data, c["volta"].data): c for c in combos}
    idas = sorted({c["ida"].data for c in combos})
    voltas = sorted({c["volta"].data for c in combos})
    melhor = combos[0]["total"]
    cab = "".join(f"<th>{dia_curto(d)}</th>" for d in voltas)
    linhas = []
    for i in idas:
        celulas = []
        for v in voltas:
            c = por_par.get((i, v))
            if not c:
                celulas.append("<td class='vazio'></td>")
                continue
            classe = faixa(c["total"], ctx["faixas"], 2)[0]
            dica = (f"Ida {c['ida'].companhias} {c['ida'].saida} ({p.reais(c['ida'].preco)}) + "
                    f"volta {c['volta'].companhias} {c['volta'].saida} ({p.reais(c['volta'].preco)})")
            estrela = " melhor" if c["total"] == melhor else ""
            celulas.append(f"<td class='c-{classe}{estrela}' title='{html.escape(dica)}'>"
                           f"<b>{p.reais(c['total'])}</b><span>{c['dias']} dias</span></td>")
        linhas.append(f"<tr><th>{dia_curto(i)}</th>{''.join(celulas)}</tr>")
    return (f"<div class='grade-wrap'><table class='grade'><thead><tr><th>Ida ↓ / Volta →</th>{cab}</tr></thead>"
            f"<tbody>{''.join(linhas)}</tbody></table></div>")


def lista_combinacoes(combos: list[dict], ctx: dict) -> str:
    linhas = []
    for n, c in enumerate(combos[:15], 1):
        i, v = c["ida"], c["volta"]
        classe, texto = faixa(c["total"], ctx["faixas"], 2)
        linhas.append(
            f"<tr><td class='num'>{n}</td>"
            f"<td><b>{dia_curto(i.data)}</b> {cia_html(i.companhias)}<br><span class='sub'>{i.saida}→{i.chegada} · "
            f"{html.escape(i.trecho)} · {p.reais(i.preco)}</span></td>"
            f"<td><b>{dia_curto(v.data)}</b> {cia_html(v.companhias)}<br><span class='sub'>{v.saida}→{v.chegada} · "
            f"{html.escape(v.trecho)} · {p.reais(v.preco)}</span></td>"
            f"<td>{c['dias']} dias</td><td class='preco n-{classe}'>{p.reais(c['total'])}</td>"
            f"<td><span class='tag {classe}'>{texto}</span></td>"
            f"<td class='links'>{links_comparar(i, v, ctx)}</td></tr>")
    return ("<div class='grade-wrap'><table class='lista'><thead><tr><th>#</th><th>Ida</th><th>Volta</th><th>Estadia</th>"
            f"<th>Total</th><th>Faixa</th><th>Comprar / comparar</th></tr></thead><tbody>{''.join(linhas)}</tbody></table></div>")


def links_comparar(i: p.Voo, v: p.Voo, ctx: dict) -> str:
    """Links para a mesma ida e volta no Google e em outros buscadores (só abrem a busca, não raspam o site)."""
    o, d = ctx["rota"]
    a = ctx["adultos"]
    sites = [
        ("Google ida", i.link),
        ("Google volta", v.link),
        ("Decolar", f"https://www.decolar.com/shop/flights/results/roundtrip/{o}/{d}/{i.data}/{v.data}/{a}/0/0"),
        ("Kayak", f"https://www.kayak.com.br/flights/{o}-{d}/{i.data}/{v.data}/{a}adults?sort=price_a"),
        ("Skyscanner", f"https://www.skyscanner.com.br/transporte/passagens-aereas/{o.lower()}/{d.lower()}/"
                       f"{i.data:%y%m%d}/{v.data:%y%m%d}/?adultsv2={a}"),
    ]
    return " · ".join(f"<a href='{html.escape(u)}' target='_blank' rel='noopener'>{n}</a>" for n, u in sites)


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--txt:#1d2433;--sub:#667085;--borda:#e4e7ec;
--promo:#6941c6;--promo-bg:#f4ebff;--bom:#067647;--bom-bg:#dcfae6;--ok:#175cd3;--ok-bg:#e0eaff;
--ruim:#b42318;--ruim-bg:#fee4e2;--aviso:#b54708;--aviso-bg:#fef0c7;--info:#344054;--info-bg:#eef2f6;--acento:#1f6fd1}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1218;--card:#181c25;--txt:#e6e9ef;
--sub:#98a2b3;--borda:#2a303c;--promo:#b692f6;--promo-bg:#2b1d4a;--bom:#47cd89;--bom-bg:#0c2e1f;--ok:#84adff;--ok-bg:#16244a;
--ruim:#f97066;--ruim-bg:#3a1513;--aviso:#fdb022;--aviso-bg:#3a2a0a;--info:#cdd5df;--info-bg:#242a35;--acento:#6aa5ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.45rem;margin:0}h2{font-size:1.1rem;margin:28px 0 10px}.sub{color:var(--sub);font-size:.85rem;margin:4px 0}
a{color:var(--acento)}
.resumo{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:16px}
.kpi{background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:12px 14px}
.kpi b{display:block;font-size:1.35rem;font-variant-numeric:tabular-nums}.kpi span{color:var(--sub);font-size:.82rem}
.kpi.destaque{border-color:var(--acento);box-shadow:0 0 0 1px var(--acento) inset}
.filtros{display:flex;gap:14px;flex-wrap:wrap;margin-top:14px;font-size:.88rem}
.dias{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(230px,1fr);gap:10px;overflow-x:auto;padding-bottom:8px}
.dia{display:flex;flex-direction:column;gap:8px}
.dia-cab{background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:8px 12px;display:flex;flex-direction:column;border-top:4px solid var(--sub)}
.dia-cab span{color:var(--sub);font-size:.8rem}.dia-cab .dia-preco{color:var(--txt);font-weight:600;font-size:.9rem}
.dia-cab.n-promo{border-top-color:var(--promo)}.dia-cab.n-bom{border-top-color:var(--bom)}
.dia-cab.n-ok{border-top-color:var(--ok)}.dia-cab.n-ruim{border-top-color:var(--ruim)}
.voo{display:block;background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:10px 12px;color:inherit;text-decoration:none}
.voo:hover{border-color:var(--acento)}
.linha1{display:flex;justify-content:space-between;align-items:center;gap:8px}
.cia{font-weight:600;font-size:.85rem;display:inline-flex;align-items:center;gap:6px}
.cia::before{content:"";width:10px;height:10px;border-radius:50%;background:var(--cia)}
.preco{font-weight:700;font-size:1.05rem;font-variant-numeric:tabular-nums;white-space:nowrap}
.n-promo{color:var(--promo)}.n-bom{color:var(--bom)}.n-ok{color:var(--ok)}.n-ruim{color:var(--ruim)}
.horas{font-size:1.15rem;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.horas sup{color:var(--aviso);font-size:.65rem;margin-left:2px}.dur{color:var(--sub);font-weight:400;font-size:.8rem;margin-left:8px}
.rota{color:var(--sub);font-size:.8rem}.tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.tag{font-size:.72rem;padding:2px 7px;border-radius:999px;background:var(--info-bg);color:var(--info);white-space:nowrap}
.tag.promo{background:var(--promo-bg);color:var(--promo)}.tag.bom{background:var(--bom-bg);color:var(--bom)}
.tag.ok{background:var(--ok-bg);color:var(--ok)}.tag.ruim{background:var(--ruim-bg);color:var(--ruim)}
.tag.aviso{background:var(--aviso-bg);color:var(--aviso)}
body.so-diretos .voo:not(.direto){display:none}body.sem-fora .voo.fora-prazo{display:none}
.grade-wrap{overflow-x:auto;border:1px solid var(--borda);border-radius:12px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{padding:7px 9px;border-bottom:1px solid var(--borda);text-align:left;vertical-align:top}
thead th{background:var(--info-bg);white-space:nowrap}
.grade th:first-child{position:sticky;left:0;background:var(--card);white-space:nowrap;z-index:1}
.grade thead th:first-child{background:var(--info-bg)}
.grade td{text-align:center;min-width:88px;font-variant-numeric:tabular-nums}
.grade td b{display:block}.grade td span{font-size:.72rem;opacity:.8}
.grade td.vazio{background:repeating-linear-gradient(45deg,transparent 0 6px,var(--info-bg) 6px 7px)}
.c-promo{background:var(--promo-bg);color:var(--promo)}.c-bom{background:var(--bom-bg);color:var(--bom)}
.c-ok{background:var(--ok-bg);color:var(--ok)}.c-ruim{background:var(--ruim-bg);color:var(--ruim)}
.grade td.melhor{outline:2px solid var(--acento);outline-offset:-2px}
.lista td.num{color:var(--sub)}.lista td.links{font-size:.8rem;min-width:150px}
.graf{width:100%;height:120px;background:var(--card);border:1px solid var(--borda);border-radius:12px}
.graf polyline{fill:none;stroke:var(--acento);stroke-width:2;vector-effect:non-scaling-stroke}
.graf-leg{display:flex;justify-content:space-between;color:var(--sub);font-size:.8rem;margin-top:4px;flex-wrap:wrap;gap:6px}
.legenda{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
"""


def gerar_painel(cfg: dict, voos: dict, combos: list[dict], hist: dict, ctx: dict, agora: datetime) -> None:
    prazo, faixas = ctx["prazo"], ctx["faixas"]
    kpis = []
    if combos:
        c = combos[0]
        i, v = c["ida"], c["volta"]
        kpis.append(f"<div class='kpi destaque'><span>Melhor ida + volta ({c['dias']} dias)</span>"
                    f"<b class='n-{faixa(c['total'], faixas, 2)[0]}'>{p.reais(c['total'])}</b>"
                    f"<span>{dia_curto(i.data)} {i.companhias} {i.saida} · {dia_curto(v.data)} {v.companhias} {v.saida}</span></div>")
    voltas_ok = [v for v in voos["volta"] if v.chega_em <= prazo]
    for nome, m in (("Ida mais barata", ctx["mais_barato"]["ida"]),
                    (f"Volta mais barata (chegando até {prazo:%d/%m})", voltas_ok[0] if voltas_ok else None)):
        if m:
            kpis.append(f"<div class='kpi'><span>{nome}</span><b class='n-{faixa(m.preco, faixas)[0]}'>{p.reais(m.preco)}</b>"
                        f"<span>{dia_curto(m.data)} · {m.companhias} · {m.saida}→{m.chegada}</span></div>")
    contagem: dict[str, int] = {}
    for v in voos["ida"] + voos["volta"]:
        texto = faixa(v.preco, faixas)[1]
        contagem[texto] = contagem.get(texto, 0) + 1
    ordem = [faixa(x, faixas)[1] for x in (faixas[0], faixas[1], faixas[2], faixas[2] + 1)]
    kpis.append("<div class='kpi'><span>Voos por faixa de preço (por trecho)</span>"
                + "".join(f"<span style='display:block'>{t}: <b style='display:inline;font-size:1rem'>"
                          f"{contagem.get(t, 0)}</b></span>" for t in ordem) + "</div>")

    legenda = "".join(f"<span class='tag {t}'>{n}</span>" for n, t in (
        (f"Até {p.reais(faixas[0])}", "promo"), (f"Até {p.reais(faixas[1])}", "bom"),
        (f"Até {p.reais(faixas[2])}", "ok"), (f"Acima de {p.reais(faixas[2])}", "ruim"),
        ("Fora do padrão", "promo"), ("Em cima da hora", "ruim"), ("Madrugada", "aviso"), ("Direto", "bom")))

    idas, voltas = periodo(cfg, ctx["hoje"])
    descricao = (f"Ida de {idas[0]:%d/%m} a {idas[-1]:%d/%m} · volta chegando até {prazo:%d/%m} · "
                 f"estadia de {cfg.get('dias_min', 1)} a {cfg.get('dias_max', 30)} dias") if idas else "Período encerrado"

    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="900">
<title>Monitor de Passagens</title><style>{CSS}</style></head><body><main>
<h1>✈ {cfg['origem']} ⇄ {cfg['destino']}</h1>
<p class="sub">{descricao}</p>
<p class="sub">Atualizado em {agora:%d/%m/%Y às %H:%M} (Brasília) · verifica a cada hora · {cfg.get('adultos', 1)} adulto(s)
· preços do Google Flights, confira no link antes de comprar</p>
<div class="resumo">{''.join(kpis)}</div>
<div class="legenda">{legenda}</div>
<h2>Combinações ida × volta (menor total de cada par de datas)</h2>
<p class="sub">Cores pelo total: roxo até {p.reais(faixas[0] * 2)} · verde até {p.reais(faixas[1] * 2)} ·
azul até {p.reais(faixas[2] * 2)} · vermelho acima. Passe o mouse para ver os voos.</p>
{tabela_combinacoes(combos, ctx)}
<h2>As 15 combinações mais baratas</h2>
{lista_combinacoes(combos, ctx)}
<div class="filtros"><label><input type="checkbox" id="f-direto"> Só voos diretos</label>
<label><input type="checkbox" id="f-prazo"> Esconder voltas fora do prazo</label></div>
{secao(f"Ida dia a dia · {cfg['origem']} → {cfg['destino']}", "ida", voos["ida"], ctx)}
{secao(f"Volta dia a dia · {cfg['destino']} → {cfg['origem']}", "volta", voos["volta"], ctx)}
<h2>Menor preço ida + volta ao longo do tempo</h2>{grafico(hist["execucoes"])}
</main><script>
for (const [id, cls] of [["f-direto", "so-diretos"], ["f-prazo", "sem-fora"]]) {{
  const el = document.getElementById(id);
  try {{ el.checked = localStorage.getItem(id) === "1"; }} catch (e) {{}}
  document.body.classList.toggle(cls, el.checked);
  el.addEventListener("change", () => {{
    document.body.classList.toggle(cls, el.checked);
    try {{ localStorage.setItem(id, el.checked ? "1" : "0"); }} catch (e) {{}}
  }});
}}
</script></body></html>"""
    PAINEL.parent.mkdir(exist_ok=True)
    PAINEL.write_text(pagina, encoding="utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p.carregar_env()
    cfg = json.loads((PASTA / "monitor.json").read_text(encoding="utf-8"))
    agora = datetime.now(BRT)
    prazo = date.fromisoformat(cfg["volta_ate"])

    voos = buscar(cfg, agora.date())
    hist = carregar_historico()
    todos = voos["ida"] + voos["volta"]
    if not todos:
        print("Nenhum voo encontrado (Google pode ter bloqueado ou o período acabou). Painel mantido.")
        return

    melhor_do_dia = {}
    for sentido in ("ida", "volta"):
        for v in voos[sentido]:
            melhor_do_dia.setdefault((sentido, v.data), v)
    combos = combinacoes(voos, cfg, prazo)
    ctx = {
        "hoje": agora.date(),
        "prazo": prazo,
        "rota": (cfg["origem"], cfg["destino"]),
        "adultos": cfg.get("adultos", 1),
        "faixas": cfg.get("faixas", [500, 700, 1500]),
        "mediana": {s: statistics.median(v.preco for v in voos[s]) if voos[s] else 1 for s in ("ida", "volta")},
        "anteriores": {k: d["atual"] for k, d in hist["precos"].items()},
        "mais_barato": {s: (voos[s][0] if voos[s] else None) for s in ("ida", "volta")},
        "melhor_do_dia": melhor_do_dia,
    }
    gerar_painel(cfg, voos, combos, hist, ctx, agora)

    # Histórico e alerta por e-mail
    total = combos[0]["total"] if combos else None
    anterior = next((e["melhor_total"] for e in reversed(hist["execucoes"]) if e.get("melhor_total")), None)
    for v in todos:
        hist["precos"][chave(v)] = {"atual": v.preco, "visto": agora.isoformat(timespec="minutes")}
    hist["execucoes"].append({"quando": agora.isoformat(timespec="minutes"), "melhor_total": total,
                              "melhor_ida": voos["ida"][0].preco if voos["ida"] else None,
                              "melhor_volta": voos["volta"][0].preco if voos["volta"] else None})
    salvar_historico(hist)

    limite = cfg.get("alerta_total")
    motivos = []
    if total and anterior and total < anterior:
        motivos.append(f"ida + volta caiu de {p.reais(anterior)} para {p.reais(total)}")
    if total and limite and total <= limite and (not anterior or anterior > limite):
        motivos.append(f"ida + volta por {p.reais(total)}, abaixo do seu limite de {p.reais(limite)}")
    cias: dict[str, int] = {}
    for v in todos:
        cias[v.companhias] = cias.get(v.companhias, 0) + 1
    print("Voos por companhia: " + ", ".join(f"{c} {n}" for c, n in sorted(cias.items(), key=lambda x: -x[1])))
    print(f"Combinações: {len(combos)} · melhor ida + volta: {p.reais(total) if total else '—'}"
          + (f" (antes {p.reais(anterior)})" if anterior else ""))
    for c in combos[:5]:
        print(f"  {p.reais(c['total']):>10}  ida {dia_curto(c['ida'].data)} {c['ida'].companhias} {c['ida'].saida}"
              f"  volta {dia_curto(c['volta'].data)} {c['volta'].companhias} {c['volta'].saida}  ({c['dias']} dias)")
    if motivos:
        print("🔔 " + "; ".join(motivos))
        if os.environ.get("EMAIL_SENHA_APP") and combos:
            i, v = combos[0]["ida"], combos[0]["volta"]
            corpo = ("; ".join(motivos) + ".\n\n"
                     f"Ida {i.data:%d/%m} {i.companhias} {i.saida}->{i.chegada} ({i.trecho}): {p.reais(i.preco)}\n  {i.link}\n"
                     f"Volta {v.data:%d/%m} {v.companhias} {v.saida}->{v.chegada} ({v.trecho}): {p.reais(v.preco)}\n  {v.link}\n\n"
                     + (f"Painel: {os.environ['PAINEL_URL']}\n" if os.environ.get("PAINEL_URL") else ""))
            p.enviar_email(f"✈ {cfg['origem']}→{cfg['destino']}: {p.reais(total)}", corpo)
    print(f"Painel: {PAINEL}")


if __name__ == "__main__":
    main()
