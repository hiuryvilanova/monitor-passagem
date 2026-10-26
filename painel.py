"""Monitor 24h: busca os voos configurados em monitor.json, guarda o histórico
e gera o painel docs/index.html (dia a dia, com horários, companhias e etiquetas).

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


def sem_repetidos(voos: list[p.Voo]) -> list[p.Voo]:
    unicos: dict[str, p.Voo] = {}
    for v in voos:
        if chave(v) not in unicos or v.preco < unicos[chave(v)].preco:
            unicos[chave(v)] = v
    return sorted(unicos.values(), key=lambda v: v.preco)


def buscar(cfg: dict) -> dict[str, list[p.Voo]]:
    args = SimpleNamespace(adultos=cfg.get("adultos", 1), criancas=cfg.get("criancas", 0),
                           max_paradas=cfg.get("max_paradas"), malas=cfg.get("malas", 0))
    trechos = {"ida": (cfg["origem"], cfg["destino"], date.fromisoformat(cfg["ida"])),
               "volta": (cfg["destino"], cfg["origem"], date.fromisoformat(cfg["volta"]))}
    resultado = {}
    for sentido, (de, para, base) in trechos.items():
        voos = []
        for dia in p.datas_flex(base, cfg.get("flex", 0)):
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


def melhor_combinacao(voos: dict, prazo: date | None):
    voltas_ok = [v for v in voos["volta"] if not prazo or v.chega_em <= prazo]
    combos = [(i.preco + v.preco, i, v) for i in voos["ida"][:30] for v in voltas_ok[:30] if v.data > i.data]
    return min(combos, key=lambda c: c[0], default=None)


def etiquetas(v: p.Voo, sentido: str, ctx: dict) -> list[tuple[str, str]]:
    """Lista de (texto, tipo). Tipos: bom, ruim, aviso, info."""
    tags = []
    if v is ctx["mais_barato"][sentido]:
        tags.append(("Mais barato", "bom"))
    elif v is ctx["melhor_do_dia"][(sentido, v.data)]:
        tags.append(("Melhor do dia", "info"))

    razao = v.preco / ctx["normal"]
    if razao <= 0.85:
        tags.append(("Abaixo do normal", "bom"))
    elif razao > 1.6:
        tags.append(("Muito caro", "ruim"))
    elif razao > 1.25:
        tags.append(("Acima do normal", "aviso"))

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
    if sentido == "volta" and ctx["prazo"] and v.chega_em > ctx["prazo"]:
        tags.append((f"Chega após {ctx['prazo']:%d/%m}", "ruim"))
    return tags


def nivel(preco: int, normal: float) -> str:
    razao = preco / normal
    return "bom" if razao <= 0.85 else "ruim" if razao > 1.6 else "aviso" if razao > 1.25 else "ok"


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
    faixa = (hi - lo) or 1
    w, h = 600, 120
    xy = [(i * w / (len(valores) - 1), h - 10 - (val - lo) * (h - 20) / faixa) for i, val in enumerate(valores)]
    linha = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    inicio = datetime.fromisoformat(pontos[0]["quando"])
    return (f"<svg viewBox='0 0 {w} {h}' class='graf' preserveAspectRatio='none' role='img' "
            f"aria-label='Menor preço ida+volta ao longo do tempo'><polyline points='{linha}' /></svg>"
            f"<div class='graf-leg'><span>{inicio:%d/%m %H:%M}</span><span>mín {p.reais(lo)} · máx {p.reais(hi)}</span>"
            f"<span>agora {p.reais(valores[-1])}</span></div>")


def cartao_voo(v: p.Voo, sentido: str, ctx: dict) -> str:
    cia = v.companhias.split(",")[0].strip()
    cor = CORES_CIA.get(cia.lower(), "#6b7280")
    tags = etiquetas(v, sentido, ctx)
    classes = " ".join(["voo", "direto" if v.paradas == 0 else "",
                        "fora-prazo" if any(t.startswith("Chega após") for t, _ in tags) else ""])
    chips = "".join(f"<span class='tag {tipo}'>{html.escape(t)}</span>" for t, tipo in tags)
    mais_um = "<sup>+1</sup>" if v.chega_em > v.data else ""
    return (f"<a class='{classes}' href='{html.escape(v.link)}' target='_blank' rel='noopener'>"
            f"<div class='linha1'><span class='cia' style='--cia:{cor}'>{html.escape(v.companhias)}</span>"
            f"<span class='preco n-{nivel(v.preco, ctx['normal'])}'>{p.reais(v.preco)}</span></div>"
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
            f"<div class='dia'><div class='dia-cab n-{nivel(menor, ctx['normal'])}'>"
            f"<b>{DIAS_SEMANA[dia.weekday()]} {dia:%d/%m}</b><span>{quando}</span>"
            f"<span class='dia-preco'>a partir de {p.reais(menor)}</span></div>"
            + "".join(cartao_voo(v, sentido, ctx) for v in lista[:8]) + "</div>")
    if not colunas:
        colunas.append("<p class='sub'>Nenhum voo encontrado nesta verificação.</p>")
    return f"<h2>{titulo}</h2><div class='dias'>{''.join(colunas)}</div>"


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--txt:#1d2433;--sub:#667085;--borda:#e4e7ec;
--bom:#067647;--bom-bg:#dcfae6;--ruim:#b42318;--ruim-bg:#fee4e2;--aviso:#b54708;--aviso-bg:#fef0c7;
--info:#344054;--info-bg:#eef2f6;--acento:#1f6fd1}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1218;--card:#181c25;--txt:#e6e9ef;
--sub:#98a2b3;--borda:#2a303c;--bom:#47cd89;--bom-bg:#0c2e1f;--ruim:#f97066;--ruim-bg:#3a1513;
--aviso:#fdb022;--aviso-bg:#3a2a0a;--info:#cdd5df;--info-bg:#242a35;--acento:#6aa5ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.45rem;margin:0}h2{font-size:1.1rem;margin:28px 0 10px}.sub{color:var(--sub);font-size:.85rem;margin:4px 0}
.resumo{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:16px}
.kpi{background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:12px 14px}
.kpi b{display:block;font-size:1.35rem;font-variant-numeric:tabular-nums}.kpi span{color:var(--sub);font-size:.82rem}
.kpi.destaque{border-color:var(--acento);box-shadow:0 0 0 1px var(--acento) inset}
.filtros{display:flex;gap:14px;flex-wrap:wrap;margin-top:14px;font-size:.88rem}
.dias{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(230px,1fr);gap:10px;overflow-x:auto;padding-bottom:8px}
.dia{display:flex;flex-direction:column;gap:8px}
.dia-cab{background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:8px 12px;display:flex;flex-direction:column;border-top:4px solid var(--sub)}
.dia-cab span{color:var(--sub);font-size:.8rem}.dia-cab .dia-preco{color:var(--txt);font-weight:600;font-size:.9rem}
.dia-cab.n-bom{border-top-color:var(--bom)}.dia-cab.n-ok{border-top-color:var(--acento)}
.dia-cab.n-aviso{border-top-color:var(--aviso)}.dia-cab.n-ruim{border-top-color:var(--ruim)}
.voo{display:block;background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:10px 12px;color:inherit;text-decoration:none}
.voo:hover{border-color:var(--acento)}
.linha1{display:flex;justify-content:space-between;align-items:center;gap:8px}
.cia{font-weight:600;font-size:.85rem;display:inline-flex;align-items:center;gap:6px}
.cia::before{content:"";width:10px;height:10px;border-radius:50%;background:var(--cia)}
.preco{font-weight:700;font-size:1.05rem;font-variant-numeric:tabular-nums}
.n-bom{color:var(--bom)}.n-aviso{color:var(--aviso)}.n-ruim{color:var(--ruim)}
.horas{font-size:1.15rem;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.horas sup{color:var(--aviso);font-size:.65rem;margin-left:2px}.dur{color:var(--sub);font-weight:400;font-size:.8rem;margin-left:8px}
.rota{color:var(--sub);font-size:.8rem}.tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.tag{font-size:.72rem;padding:2px 7px;border-radius:999px;background:var(--info-bg);color:var(--info);white-space:nowrap}
.tag.bom{background:var(--bom-bg);color:var(--bom)}.tag.ruim{background:var(--ruim-bg);color:var(--ruim)}
.tag.aviso{background:var(--aviso-bg);color:var(--aviso)}
body.so-diretos .voo:not(.direto){display:none}body.sem-fora .voo.fora-prazo{display:none}
.graf{width:100%;height:120px;background:var(--card);border:1px solid var(--borda);border-radius:12px}
.graf polyline{fill:none;stroke:var(--acento);stroke-width:2;vector-effect:non-scaling-stroke}
.graf-leg{display:flex;justify-content:space-between;color:var(--sub);font-size:.8rem;margin-top:4px;flex-wrap:wrap;gap:6px}
.legenda{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
"""


def gerar_painel(cfg: dict, voos: dict, hist: dict, ctx: dict, agora: datetime) -> None:
    combo = ctx["combo"]
    prazo = ctx["prazo"]
    kpis = []
    if combo:
        total, i, v = combo
        kpis.append(f"<div class='kpi destaque'><span>Melhor ida + volta{f' (chegando até {prazo:%d/%m})' if prazo else ''}</span>"
                    f"<b>{p.reais(total)}</b><span>{DIAS_SEMANA[i.data.weekday()]} {i.data:%d/%m} {i.companhias} {i.saida} · "
                    f"{DIAS_SEMANA[v.data.weekday()]} {v.data:%d/%m} {v.companhias} {v.saida}</span></div>")
    voltas_ok = [v for v in voos["volta"] if not prazo or v.chega_em <= prazo]
    nome_volta = f"Volta mais barata (chegando até {prazo:%d/%m})" if prazo else "Volta mais barata"
    for nome, m in (("Ida mais barata", ctx["mais_barato"]["ida"]),
                    (nome_volta, voltas_ok[0] if voltas_ok else None)):
        if m:
            kpis.append(f"<div class='kpi'><span>{nome}</span><b class='n-{nivel(m.preco, ctx['normal'])}'>{p.reais(m.preco)}</b>"
                        f"<span>{m.data:%d/%m} · {m.companhias} · {m.saida}→{m.chegada}</span></div>")
    kpis.append(f"<div class='kpi'><span>Preço normal da rota (referência)</span><b>{p.reais(int(ctx['normal']))}</b>"
                f"<span>por trecho · base das etiquetas de preço</span></div>")

    legenda = "".join(f"<span class='tag {t}'>{n}</span>" for n, t in (
        ("Abaixo do normal", "bom"), ("Acima do normal", "aviso"), ("Muito caro", "ruim"),
        ("Em cima da hora", "ruim"), ("Madrugada", "aviso"), ("Direto", "bom")))

    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="900">
<title>Monitor de Passagens</title><style>{CSS}</style></head><body><main>
<h1>✈ {cfg['origem']} ⇄ {cfg['destino']}</h1>
<p class="sub">Atualizado em {agora:%d/%m/%Y às %H:%M} (Brasília) · verifica a cada hora · {cfg.get('adultos', 1)} adulto(s)
· preços do Google Flights, confira no link antes de comprar</p>
<div class="resumo">{''.join(kpis)}</div>
<div class="filtros"><label><input type="checkbox" id="f-direto"> Só voos diretos</label>
<label><input type="checkbox" id="f-prazo"> Esconder voltas fora do prazo</label></div>
<div class="legenda">{legenda}</div>
{secao(f"Ida · {cfg['origem']} → {cfg['destino']}", "ida", voos["ida"], ctx)}
{secao(f"Volta · {cfg['destino']} → {cfg['origem']}", "volta", voos["volta"], ctx)}
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
    prazo = date.fromisoformat(cfg["chegar_ate"]) if cfg.get("chegar_ate") else None

    voos = buscar(cfg)
    hist = carregar_historico()
    todos = voos["ida"] + voos["volta"]
    if not todos:
        print("Nenhum voo encontrado (Google pode ter bloqueado). Painel mantido como estava.")
        return

    melhor_do_dia = {}
    for sentido in ("ida", "volta"):
        for v in voos[sentido]:
            melhor_do_dia.setdefault((sentido, v.data), v)
    ctx = {
        "hoje": agora.date(),
        "prazo": prazo,
        "normal": cfg.get("preco_normal") or statistics.median(v.preco for v in todos),
        "anteriores": {k: d["atual"] for k, d in hist["precos"].items()},
        "mais_barato": {s: (voos[s][0] if voos[s] else None) for s in ("ida", "volta")},
        "melhor_do_dia": melhor_do_dia,
        "combo": melhor_combinacao(voos, prazo),
    }
    gerar_painel(cfg, voos, hist, ctx, agora)

    # Histórico e alerta por e-mail
    total = ctx["combo"][0] if ctx["combo"] else None
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
    print(f"Melhor ida + volta: {p.reais(total) if total else '—'}" + (f" (antes {p.reais(anterior)})" if anterior else ""))
    if motivos:
        print("🔔 " + "; ".join(motivos))
        if os.environ.get("EMAIL_SENHA_APP"):
            _, i, v = ctx["combo"]
            corpo = ("; ".join(motivos) + ".\n\n"
                     f"Ida {i.data:%d/%m} {i.companhias} {i.saida}->{i.chegada} ({i.trecho}): {p.reais(i.preco)}\n  {i.link}\n"
                     f"Volta {v.data:%d/%m} {v.companhias} {v.saida}->{v.chegada} ({v.trecho}): {p.reais(v.preco)}\n  {v.link}\n\n"
                     + (f"Painel: {os.environ['PAINEL_URL']}\n" if os.environ.get("PAINEL_URL") else ""))
            p.enviar_email(f"✈ {cfg['origem']}→{cfg['destino']}: {p.reais(total)}", corpo)
    print(f"Painel: {PAINEL}")


if __name__ == "__main__":
    main()
