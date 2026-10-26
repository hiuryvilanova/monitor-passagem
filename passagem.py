"""Busca passagens aéreas (Google Flights) e hospedagem (Google Hotels) mais baratas.

Exemplo:
    python passagem.py --origem BSB --destino THE --ida 2026-11-20 --volta 2026-11-25 --flex 2
"""

import argparse
import csv
import html
import json
import os
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote_plus

import requests
from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query, get_flights

PASTA = Path(__file__).parent
PAUSA_ENTRE_BUSCAS = 1.5  # segundos, para não ser bloqueado pelo Google

CIDADES = {
    "BSB": "Brasília", "THE": "Teresina", "GRU": "São Paulo", "CGH": "São Paulo",
    "VCP": "Campinas", "GIG": "Rio de Janeiro", "SDU": "Rio de Janeiro", "CNF": "Belo Horizonte",
    "SSA": "Salvador", "REC": "Recife", "FOR": "Fortaleza", "SLZ": "São Luís", "NAT": "Natal",
    "JPA": "João Pessoa", "MCZ": "Maceió", "AJU": "Aracaju", "BEL": "Belém", "MAO": "Manaus",
    "GYN": "Goiânia", "CGB": "Cuiabá", "CGR": "Campo Grande", "CWB": "Curitiba",
    "FLN": "Florianópolis", "POA": "Porto Alegre", "VIX": "Vitória", "PMW": "Palmas",
}


@dataclass
class Voo:
    data: date
    preco: int
    companhias: str
    paradas: int
    saida: str
    chegada: str
    chega_em: date
    duracao_min: int
    trecho: str
    link: str


@dataclass
class Hotel:
    nome: str
    preco_noite: int
    preco_total: int
    nota: float | None
    tipo: str
    link: str


def carregar_env() -> None:
    """Lê variáveis do arquivo .env da pasta do script (ex.: SERPAPI_KEY=...)."""
    arquivo = PASTA / ".env"
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        if "=" in linha and not linha.strip().startswith("#"):
            chave, valor = linha.split("=", 1)
            os.environ.setdefault(chave.strip(), valor.strip().strip('"'))


def fmt_hora(dt) -> str:
    return f"{dt.time[0]:02d}:{dt.time[1]:02d}"


def buscar_voos(origem: str, destino: str, dia: date, args) -> list[Voo]:
    query = create_query(
        flights=[FlightQuery(date=dia.isoformat(), from_airport=origem, to_airport=destino)],
        seat="economy",
        trip="one-way",
        passengers=Passengers(adults=args.adultos, children=args.criancas),
        language="pt-BR",
        currency="BRL",
        max_stops=args.max_paradas,
        checked_bags=args.malas,
    )
    try:
        resultados = get_flights(query)
    except FlightsNotFound:
        return []
    except Exception as erro:
        print(f"   ! erro buscando {origem}->{destino} em {dia}: {erro}")
        return []

    voos = []
    for r in resultados:
        trechos = r.flights
        if not trechos or not r.price:
            continue
        voos.append(Voo(
            data=dia,
            preco=r.price,
            companhias=", ".join(r.airlines),
            paradas=len(trechos) - 1,
            saida=fmt_hora(trechos[0].departure),
            chegada=fmt_hora(trechos[-1].arrival),
            chega_em=date(*trechos[-1].arrival.date),
            duracao_min=sum(t.duration for t in trechos),
            trecho=" > ".join([trechos[0].from_airport.code] + [t.to_airport.code for t in trechos]),
            link=query.url(),
        ))
    return sorted(voos, key=lambda v: v.preco)


def buscar_hoteis(cidade: str, entrada: date, saida: date, args) -> list[Hotel]:
    chave = os.environ.get("SERPAPI_KEY")
    if not chave:
        return []
    params = {
        "engine": "google_hotels",
        "q": f"hotéis em {cidade}",
        "check_in_date": entrada.isoformat(),
        "check_out_date": saida.isoformat(),
        "adults": args.adultos,
        "children": args.criancas,
        "currency": "BRL",
        "gl": "br",
        "hl": "pt-br",
        "sort_by": 3,  # menor preço
        "api_key": chave,
    }
    if args.nota_min:
        params["rating"] = {3.5: 7, 4.0: 8, 4.5: 9}.get(args.nota_min, 7)
    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
        resp.raise_for_status()
        dados = resp.json()
    except Exception as erro:
        print(f"   ! erro buscando hotéis: {erro}")
        return []

    noites = (saida - entrada).days
    hoteis = []
    for p in dados.get("properties", []):
        noite = (p.get("rate_per_night") or {}).get("extracted_lowest")
        if not noite:
            continue
        total = (p.get("total_rate") or {}).get("extracted_lowest") or noite * noites
        hoteis.append(Hotel(
            nome=p.get("name", "?"),
            preco_noite=int(noite),
            preco_total=int(total),
            nota=p.get("overall_rating"),
            tipo=p.get("type", ""),
            link=p.get("link") or p.get("serpapi_property_details_link", ""),
        ))
    return sorted(hoteis, key=lambda h: h.preco_total)


def links_hotel(cidade: str, entrada: date, saida: date, adultos: int) -> dict[str, str]:
    return {
        "Google Hotels": f"https://www.google.com/travel/search?q={quote_plus('hotéis em ' + cidade)}"
                         f"&qs=&checkin={entrada}&checkout={saida}&hl=pt-BR&curr=BRL",
        "Booking": f"https://www.booking.com/searchresults.pt-br.html?ss={quote_plus(cidade)}"
                   f"&checkin={entrada}&checkout={saida}&group_adults={adultos}&selected_currency=BRL&order=price",
        "Airbnb": f"https://www.airbnb.com.br/s/{quote_plus(cidade)}/homes?checkin={entrada}"
                  f"&checkout={saida}&adults={adultos}",
    }


def atualizar_historico(rota: str, preco: int) -> int | None:
    """Guarda o menor preço desta rota e devolve o preço da verificação anterior."""
    arquivo = PASTA / "resultados" / "historico.json"
    arquivo.parent.mkdir(exist_ok=True)
    historico = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}
    registros = historico.setdefault(rota, [])
    anterior = registros[-1]["preco"] if registros else None
    registros.append({"quando": datetime.now().isoformat(timespec="minutes"), "preco": preco})
    arquivo.write_text(json.dumps(historico, ensure_ascii=False, indent=2), encoding="utf-8")
    return anterior


def texto_alerta(motivos, voos_ida, voos_volta, combos) -> str:
    linhas = ["Alerta de passagem: " + "; ".join(motivos) + ".", ""]
    if combos:
        c = min(combos, key=lambda c: c["voos"])
        linhas += [f"Melhor combinação: ida {c['ida'].data:%d/%m} ({c['ida'].companhias}, {c['ida'].trecho}, "
                   f"{c['ida'].saida}) + volta {c['volta'].data:%d/%m} ({c['volta'].companhias}, {c['volta'].trecho}, "
                   f"{c['volta'].saida}) = {reais(c['voos'])}", ""]
    for titulo, voos in (("IDA", voos_ida), ("VOLTA", voos_volta)):
        if voos:
            linhas.append(f"{titulo} mais barata:")
            linhas += [f"  {v.data:%d/%m}  {reais(v.preco)}  {v.companhias}  {v.trecho}  {v.saida}->{v.chegada}"
                       for v in voos[:5]]
            linhas += [f"  Comprar: {voos[0].link}", ""]
    linhas.append("Preços do Google Flights; confira no link antes de comprar.")
    return "\n".join(linhas)


def enviar_email(assunto: str, corpo: str) -> None:
    remetente = os.environ.get("EMAIL_REMETENTE")
    senha = os.environ.get("EMAIL_SENHA_APP")
    para = os.environ.get("EMAIL_PARA") or remetente
    if not (remetente and senha):
        print("   ! E-mail não enviado: preencha EMAIL_REMETENTE e EMAIL_SENHA_APP no .env")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = assunto, remetente, para
    msg.set_content(corpo)
    try:
        with smtplib.SMTP_SSL(os.environ.get("EMAIL_SMTP", "smtp.gmail.com"), 465, timeout=30) as smtp:
            smtp.login(remetente, senha)
            smtp.send_message(msg)
        print(f"   ✉ E-mail enviado para {para}")
    except Exception as erro:
        print(f"   ! Falha ao enviar e-mail: {erro}")


def datas_flex(base: date, flex: int) -> list[date]:
    hoje = date.today()
    return [d for d in (base + timedelta(days=i) for i in range(-flex, flex + 1)) if d >= hoje]


def reais(valor: int) -> str:
    return f"R$ {valor:,.0f}".replace(",", ".")


def gerar_html(arquivo: Path, args, combos, voos_ida, voos_volta, hoteis, links, cidade) -> None:
    def linha_voo(v: Voo) -> str:
        return (f"<tr><td>{v.data:%d/%m (%a)}</td><td>{reais(v.preco)}</td><td>{html.escape(v.companhias)}</td>"
                f"<td>{v.trecho}</td><td>{v.saida} → {v.chegada}</td><td>{v.duracao_min // 60}h{v.duracao_min % 60:02d}</td>"
                f"<td><a href='{html.escape(v.link)}' target='_blank'>ver</a></td></tr>")

    cab_voo = "<tr><th>Data</th><th>Preço</th><th>Cia</th><th>Rota</th><th>Horário</th><th>Duração</th><th></th></tr>"
    partes = [f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Viagem {args.origem} → {args.destino}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#222;background:#fafafa}}
h1{{font-size:1.5rem}} h2{{margin-top:2rem;font-size:1.15rem}} table{{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem}}
th,td{{padding:6px 8px;border-bottom:1px solid #e5e5e5;text-align:left}} th{{background:#f0f0f0}}
.top td{{font-weight:600;background:#eefbea}} .nota{{color:#666;font-size:.85rem}} a{{color:#0b57d0}}
.wrap{{overflow-x:auto}}</style></head><body>
<h1>{args.origem} → {args.destino} · {args.adultos} adulto(s){f', {args.criancas} criança(s)' if args.criancas else ''}</h1>
<p class="nota">Gerado em {date.today():%d/%m/%Y}. Preços do Google Flights/Hotels, podem mudar a qualquer momento — confira no link antes de comprar.</p>"""]

    if combos:
        partes.append("<h2>Melhores combinações (voo ida + volta + hotel)</h2><div class='wrap'><table>"
                      "<tr><th>Ida</th><th>Volta</th><th>Noites</th><th>Voos</th><th>Hotel (est.)</th><th>Total</th></tr>")
        for i, c in enumerate(combos[:15]):
            partes.append(f"<tr class='{'top' if i == 0 else ''}'><td>{c['ida'].data:%d/%m} {html.escape(c['ida'].companhias)}</td>"
                          f"<td>{c['volta'].data:%d/%m} {html.escape(c['volta'].companhias)}</td><td>{c['noites']}</td>"
                          f"<td>{reais(c['voos'])}</td><td>{reais(c['hotel']) if c['hotel'] else '—'}</td><td>{reais(c['total'])}</td></tr>")
        partes.append("</table></div>")

    for titulo, voos in (("Voos de ida", voos_ida), ("Voos de volta", voos_volta)):
        if voos:
            partes.append(f"<h2>{titulo} (mais baratos)</h2><div class='wrap'><table>{cab_voo}")
            partes += [linha_voo(v) for v in voos[:20]]
            partes.append("</table></div>")

    partes.append(f"<h2>Hospedagem em {html.escape(cidade)}</h2>")
    if hoteis:
        partes.append("<div class='wrap'><table><tr><th>Nome</th><th>Tipo</th><th>Nota</th><th>Por noite</th><th>Total</th><th></th></tr>")
        for h in hoteis[:20]:
            partes.append(f"<tr><td>{html.escape(h.nome)}</td><td>{html.escape(h.tipo)}</td><td>{h.nota or '—'}</td>"
                          f"<td>{reais(h.preco_noite)}</td><td>{reais(h.preco_total)}</td>"
                          f"<td><a href='{html.escape(h.link)}' target='_blank'>ver</a></td></tr>")
        partes.append("</table></div>")
    else:
        partes.append("<p class='nota'>Sem SERPAPI_KEY no .env — use os links abaixo para ver os preços.</p>")
    partes.append("<p>" + " · ".join(f"<a href='{html.escape(u)}' target='_blank'>{n}</a>" for n, u in links.items()) + "</p>")
    partes.append("</body></html>")
    arquivo.write_text("\n".join(partes), encoding="utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    carregar_env()

    ap = argparse.ArgumentParser(description="Busca passagens e hospedagem mais baratas.")
    ap.add_argument("--origem", required=True, help="Aeroporto de origem (ex.: BSB)")
    ap.add_argument("--destino", required=True, help="Aeroporto de destino (ex.: THE)")
    ap.add_argument("--ida", required=True, type=date.fromisoformat, help="Data de ida AAAA-MM-DD")
    ap.add_argument("--volta", type=date.fromisoformat, help="Data de volta AAAA-MM-DD (vazio = só ida)")
    ap.add_argument("--flex", type=int, default=0, help="Testar N dias antes/depois de cada data (padrão 0)")
    ap.add_argument("--adultos", type=int, default=1)
    ap.add_argument("--criancas", type=int, default=0)
    ap.add_argument("--max-paradas", type=int, default=None, help="0 = só voo direto")
    ap.add_argument("--malas", type=int, default=0, help="Malas despachadas a incluir no preço")
    ap.add_argument("--cidade-hotel", help="Cidade para hospedagem (padrão: cidade do destino)")
    ap.add_argument("--nota-min", type=float, choices=[3.5, 4.0, 4.5], help="Nota mínima do hotel")
    ap.add_argument("--sem-hotel", action="store_true", help="Não buscar hospedagem")
    ap.add_argument("--alerta", type=int, help="Avisar se ida + volta mais baratas custarem até este valor (R$)")
    ap.add_argument("--email", action="store_true", help="Enviar o alerta por e-mail (configure o .env)")
    args = ap.parse_args()
    args.origem, args.destino = args.origem.upper(), args.destino.upper()

    if args.volta and args.volta <= args.ida:
        ap.error("--volta precisa ser depois de --ida")

    cidade = args.cidade_hotel or CIDADES.get(args.destino) or args.destino

    # --- Voos ---
    voos_ida: list[Voo] = []
    for dia in datas_flex(args.ida, args.flex):
        print(f"✈  Buscando ida   {args.origem}->{args.destino} em {dia:%d/%m}...")
        voos_ida += buscar_voos(args.origem, args.destino, dia, args)
        time.sleep(PAUSA_ENTRE_BUSCAS)
    voos_ida.sort(key=lambda v: v.preco)

    voos_volta: list[Voo] = []
    if args.volta:
        for dia in datas_flex(args.volta, args.flex):
            print(f"✈  Buscando volta {args.destino}->{args.origem} em {dia:%d/%m}...")
            voos_volta += buscar_voos(args.destino, args.origem, dia, args)
            time.sleep(PAUSA_ENTRE_BUSCAS)
        voos_volta.sort(key=lambda v: v.preco)

    # --- Hospedagem (uma busca nas datas informadas, para economizar a cota da SerpApi) ---
    hoteis: list[Hotel] = []
    if args.volta and not args.sem_hotel:
        if os.environ.get("SERPAPI_KEY"):
            print(f"🏨 Buscando hospedagem em {cidade}...")
        hoteis = buscar_hoteis(cidade, args.ida, args.volta, args)
    preco_noite = hoteis[0].preco_noite if hoteis else 0

    # --- Combinações: melhor voo de cada dia de ida x melhor de cada dia de volta ---
    melhor_por_dia = lambda voos: {v.data: v for v in sorted(voos, key=lambda v: -v.preco)}
    combos = []
    if voos_volta:
        for ida in melhor_por_dia(voos_ida).values():
            for volta in melhor_por_dia(voos_volta).values():
                noites = (volta.data - ida.data).days
                if noites < 1:
                    continue
                hotel = preco_noite * noites
                combos.append({"ida": ida, "volta": volta, "noites": noites,
                               "voos": ida.preco + volta.preco, "hotel": hotel,
                               "total": ida.preco + volta.preco + hotel})
        combos.sort(key=lambda c: c["total"])

    # --- Resultado no terminal ---
    print()
    if not voos_ida:
        print("Nenhum voo encontrado. Confira os códigos dos aeroportos e as datas.")
    else:
        print("=== Ida mais barata ===")
        for v in voos_ida[:5]:
            print(f"  {v.data:%d/%m}  {reais(v.preco):>10}  {v.companhias:<15} {v.trecho:<20} {v.saida}->{v.chegada}")
    if voos_volta:
        print("=== Volta mais barata ===")
        for v in voos_volta[:5]:
            print(f"  {v.data:%d/%m}  {reais(v.preco):>10}  {v.companhias:<15} {v.trecho:<20} {v.saida}->{v.chegada}")
    if hoteis:
        print(f"=== Hospedagem em {cidade} ({args.ida:%d/%m}–{args.volta:%d/%m}) ===")
        for h in hoteis[:5]:
            print(f"  {reais(h.preco_noite):>10}/noite  {reais(h.preco_total):>10} total  nota {h.nota or '-'}  {h.nome}")
    if combos:
        c = combos[0]
        print("\n>>> MELHOR PACOTE:")
        print(f"    Ida {c['ida'].data:%d/%m} ({c['ida'].companhias}) + volta {c['volta'].data:%d/%m} ({c['volta'].companhias})"
              f" + {c['noites']} noites = {reais(c['total'])}")

    # --- Alerta: preço abaixo do limite ou menor que na última verificação ---
    melhor_voos = min((c["voos"] for c in combos), default=voos_ida[0].preco if voos_ida else None)
    if melhor_voos is not None:
        rota = f"{args.origem}-{args.destino} {args.ida}" + (f" a {args.volta}" if args.volta else "")
        anterior = atualizar_historico(rota, melhor_voos)
        motivos = []
        if args.alerta and melhor_voos <= args.alerta:
            motivos.append(f"voos por {reais(melhor_voos)}, abaixo do seu limite de {reais(args.alerta)}")
        if anterior and melhor_voos < anterior:
            motivos.append(f"preço caiu de {reais(anterior)} para {reais(melhor_voos)}")
        if motivos:
            print("\n🔔 ALERTA: " + "; ".join(motivos))
            if args.email:
                enviar_email(f"✈ Alerta de passagem {args.origem}→{args.destino}: {reais(melhor_voos)}",
                             texto_alerta(motivos, voos_ida, voos_volta, combos))
        else:
            print(f"\nSem alerta (melhor preço dos voos: {reais(melhor_voos)}"
                  + (f", antes {reais(anterior)}" if anterior else "") + ").")

    # --- Arquivos ---
    saida = PASTA / "resultados"
    saida.mkdir(exist_ok=True)
    nome = f"{args.origem}-{args.destino}_{args.ida}"
    with open(saida / f"{nome}_voos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["sentido", "data", "preco", "companhias", "paradas", "rota", "saida", "chegada", "duracao_min", "link"])
        for sentido, voos in (("ida", voos_ida), ("volta", voos_volta)):
            for v in voos:
                w.writerow([sentido, v.data, v.preco, v.companhias, v.paradas, v.trecho, v.saida, v.chegada, v.duracao_min, v.link])

    links = links_hotel(cidade, args.ida, args.volta or args.ida + timedelta(days=1), args.adultos)
    relatorio = saida / f"{nome}.html"
    gerar_html(relatorio, args, combos, voos_ida, voos_volta, hoteis, links, cidade)
    print(f"\nRelatório: {relatorio}")
    print(f"Planilha:  {saida / (nome + '_voos.csv')}")


if __name__ == "__main__":
    main()
