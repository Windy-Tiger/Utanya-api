"""
BODIVA Bulletin Structured Parser
Uses Claude vision to extract structured data from image-based PDF tables.
Each section becomes a precise, queryable chunk with exact metadata.
"""

import fitz
import base64
import json
import os
import re
from anthropic import Anthropic


def render_page(pdf_path: str, page_num: int, zoom: float = 1.8) -> str:
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img_b64 = base64.b64encode(pix.tobytes("png")).decode()
    doc.close()
    return img_b64


def ask_vision(client: Anthropic, img_b64: str, prompt: str) -> dict:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    )
    text = response.content[0].text.strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "parse_error": True}


def extract_bulletin_number_and_date(pdf_path: str) -> tuple:
    doc = fitz.open(pdf_path)
    text = doc[1].get_text()
    doc.close()

    normalized = re.sub(r'\s+', ' ', text)
    text_joined = re.sub(r'(\d)\s(\d)', r'\1\2', normalized)
    text_joined = re.sub(r'(\d)\s(\d)', r'\1\2', text_joined)

    num_match = re.search(r'Boletim de Mercado\s*N[º°]\s*(\d+)', text_joined)
    bulletin_number = num_match.group(1) if num_match else "unknown"

    month_map = {
        'Janeiro': '01', 'Fevereiro': '02', 'Março': '03', 'Abril': '04',
        'Maio': '05', 'Junho': '06', 'Julho': '07', 'Agosto': '08',
        'Setembro': '09', 'Outubro': '10', 'Novembro': '11', 'Dezembro': '12'
    }

    date_match = re.search(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', text_joined)
    if date_match:
        day = date_match.group(1).zfill(2)
        month = month_map.get(date_match.group(2), '01')
        year = date_match.group(3)
        date_str = f"{year}-{month}-{day}"
    else:
        date_str = "unknown"

    return bulletin_number, date_str


def parse_session_summary(client, pdf_path, bulletin_num, date):
    img = render_page(pdf_path, 2)
    data = ask_vision(client, img, """Extract ALL monetary values from the Resumo de Mercado table.
Return ONLY this JSON (numbers only, no currency symbols, use 0 for zero):
{"total_sessao":"","mercado_titulos_tesouro_total":"","multilateral_total":"","multilateral_ot_nr":"","multilateral_ot_me":"","bilateral_total":"","bilateral_ot_nr":"","bilateral_ot_me":"","mercado_obrigacoes_privadas":"","mercado_acoes":"","mercado_operacoes_reporte":"","mercado_uniparticipacao":""}""")

    content = f"""BODIVA Boletim {bulletin_num} - {date}
QUADRO RESUMO DA SESSÃO

Total Negociado: AOA {data.get('total_sessao','N/A')}
Mercado Títulos Tesouro Total: AOA {data.get('mercado_titulos_tesouro_total','N/A')}
  Multilateral (Bolsa) Total: AOA {data.get('multilateral_total','N/A')}
    OT-NR (bolsa): AOA {data.get('multilateral_ot_nr','N/A')}
    OT-ME (bolsa): AOA {data.get('multilateral_ot_me','N/A')}
  Bilateral (OTC) Total: AOA {data.get('bilateral_total','N/A')}
    OT-NR (OTC): AOA {data.get('bilateral_ot_nr','N/A')}
    OT-ME (OTC): AOA {data.get('bilateral_ot_me','N/A')}
Mercado Obrigações Privadas: AOA {data.get('mercado_obrigacoes_privadas','N/A')}
Mercado de Acções (MBA): AOA {data.get('mercado_acoes','N/A')}
Mercado de Operações de Reporte: AOA {data.get('mercado_operacoes_reporte','N/A')}
Mercado Unidades Participação: AOA {data.get('mercado_uniparticipacao','0')}"""

    return [{"content": content, "metadata": {"section": "quadro_resumo", "bulletin": bulletin_num, "date": date, "data": data}}]


def parse_member_performance(client, pdf_path, bulletin_num, date):
    img = render_page(pdf_path, 3)
    data = ask_vision(client, img, """Extract the member performance (Desempenho dos Membros) table.
Return ONLY this JSON:
{"total_montante":"","total_negocios":"","members":[{"code":"","name":"","montante":"","negocios":"","percentagem":"","vendas_interbancarias":"","compras_interbancarias":"","negocios_internos":""}]}
Include ALL members. Return only valid JSON.""")

    members = data.get('members', [])
    lines = [
        f"{m.get('code','?')} ({m.get('name','?')}): AOA {m.get('montante','?')} | {m.get('negocios','?')} negócios | {m.get('percentagem','?')}% quota | Vendas: AOA {m.get('vendas_interbancarias','?')} | Compras: AOA {m.get('compras_interbancarias','?')} | Internos: AOA {m.get('negocios_internos','?')}"
        for m in members
    ]

    content = f"""BODIVA Boletim {bulletin_num} - {date}
DESEMPENHO DOS MEMBROS DE NEGOCIAÇÃO

Total: AOA {data.get('total_montante','N/A')} | {data.get('total_negocios','N/A')} negócios

""" + "\n".join(lines)

    return [{"content": content, "metadata": {"section": "desempenho_membros", "bulletin": bulletin_num, "date": date, "members": members}}]


def parse_otnr_table(client, pdf_path, bulletin_num, date):
    img = render_page(pdf_path, 4)
    data = ask_vision(client, img, """Extract the complete OT-NR trading table from this BODIVA bulletin page.
Return ONLY this JSON:
{"total_negocios":"","total_volume":"","bonds":[{"codigo":"","data_emissao":"","data_maturidade":"","cupao":"","ytm":"","negocios":"","volume":"","preco_abertura":"","cotacao_anterior":"","cotacao_actual":"","variacao":""}]}
Include ALL bonds listed. Return only valid JSON.""")

    bonds = data.get('bonds', [])
    chunks = []

    for bond in bonds:
        codigo = bond.get('codigo', 'unknown')
        content = f"""BODIVA Boletim {bulletin_num} - {date}
OT-NR: {codigo}

Código: {codigo}
Emissão: {bond.get('data_emissao','N/A')} | Maturidade: {bond.get('data_maturidade','N/A')}
Taxa Cupão: {bond.get('cupao','N/A')} | YTM: {bond.get('ytm','N/A')}
Negócios: {bond.get('negocios','N/A')} | Volume: {bond.get('volume','N/A')} unidades (Kz 1.000 nominal)
Cotação Anterior: {bond.get('cotacao_anterior','N/A')}% | Cotação Actual: {bond.get('cotacao_actual','N/A')}%
Variação: {bond.get('variacao','N/A')}"""
        chunks.append({"content": content, "metadata": {"section": "otnr_bond", "instrument_code": codigo, "bulletin": bulletin_num, "date": date, "cupao": bond.get('cupao'), "ytm": bond.get('ytm'), "variacao": bond.get('variacao'), "cotacao": bond.get('cotacao_actual')}})

    summary_lines = [f"{b.get('codigo','?')}: cupão {b.get('cupao','?')}, YTM {b.get('ytm','?')}, preço {b.get('cotacao_actual','?')}%, var {b.get('variacao','?')}, vol {b.get('volume','?')}" for b in bonds]
    chunks.insert(0, {"content": f"""BODIVA Boletim {bulletin_num} - {date}
OT-NR MERCADO DE BOLSA RESUMO ({len(bonds)} instrumentos)
Total: {data.get('total_negocios','?')} negócios | {data.get('total_volume','?')} unidades

""" + "\n".join(summary_lines), "metadata": {"section": "otnr_summary", "bulletin": bulletin_num, "date": date, "total_bonds": len(bonds)}})

    return chunks


def parse_stocks_and_others(client, pdf_path, bulletin_num, date):
    img = render_page(pdf_path, 5)
    data = ask_vision(client, img, """Extract ALL tables from this BODIVA bulletin page: OT-ME bonds, corporate bonds, and stocks.
Return ONLY this JSON:
{"otme_bonds":[{"codigo":"","cupao":"","ytm":"","negocios":"","volume":"","cotacao_anterior":"","cotacao_actual":"","variacao":""}],"corporate_bonds":[{"codigo":"","cupao":"","ytm":"","negocios":"","volume":"","cotacao_anterior":"","cotacao_actual":"","variacao":""}],"stocks":[{"codigo":"","nome":"","negocios":"","volume":"","cotacao_anterior":"","cotacao_actual":"","variacao":"","capitalizacao_bolsista":""}],"total_stocks_negocios":"","total_stocks_volume":"","total_capitalizacao":""}
Use empty arrays [] for sections with no transactions. Return only valid JSON.""")

    chunks = []

    otme = data.get('otme_bonds', [])
    if otme:
        lines = [f"{b.get('codigo','?')}: cupão {b.get('cupao','?')}, YTM {b.get('ytm','?')}, preço {b.get('cotacao_actual','?')}%, var {b.get('variacao','?')}" for b in otme]
        chunks.append({"content": f"BODIVA Boletim {bulletin_num} - {date}\nOT-ME MERCADO DE BOLSA\n\n" + "\n".join(lines), "metadata": {"section": "otme_exchange", "bulletin": bulletin_num, "date": date}})
    else:
        chunks.append({"content": f"BODIVA Boletim {bulletin_num} - {date}\nOT-ME Mercado de Bolsa: Não se registaram transacções.", "metadata": {"section": "otme_exchange", "bulletin": bulletin_num, "date": date}})

    corp = data.get('corporate_bonds', [])
    if corp:
        lines = [f"{b.get('codigo','?')}: cupão {b.get('cupao','?')}, YTM {b.get('ytm','?')}, preço {b.get('cotacao_actual','?')}%, var {b.get('variacao','?')}, vol {b.get('volume','?')}" for b in corp]
        chunks.append({"content": f"BODIVA Boletim {bulletin_num} - {date}\nMERCADO OBRIGAÇÕES PRIVADAS\n\n" + "\n".join(lines), "metadata": {"section": "corporate_bonds", "bulletin": bulletin_num, "date": date}})

    stocks = data.get('stocks', [])
    for stock in stocks:
        codigo = stock.get('codigo', 'unknown')
        chunks.append({"content": f"""BODIVA Boletim {bulletin_num} - {date}
ACÇÃO: {codigo} - {stock.get('nome','')}

Código: {codigo} | Nome: {stock.get('nome','N/A')}
Negócios: {stock.get('negocios','N/A')} | Volume: {stock.get('volume','N/A')} acções
Cotação Anterior: AOA {stock.get('cotacao_anterior','N/A')} | Cotação Actual: AOA {stock.get('cotacao_actual','N/A')}
Variação: {stock.get('variacao','N/A')}
Capitalização Bolsista: AOA {stock.get('capitalizacao_bolsista','N/A')}""",
        "metadata": {"section": "stock", "instrument_code": codigo, "bulletin": bulletin_num, "date": date, "variacao": stock.get('variacao'), "preco": stock.get('cotacao_actual'), "capitalizacao": stock.get('capitalizacao_bolsista')}})

    if stocks:
        stock_lines = [f"{s.get('codigo','?')} ({s.get('nome','?')}): AOA {s.get('cotacao_actual','?')} | {s.get('variacao','?')} | Cap: AOA {s.get('capitalizacao_bolsista','?')}" for s in stocks]
        chunks.insert(0, {"content": f"""BODIVA Boletim {bulletin_num} - {date}
MERCADO DE ACÇÕES RESUMO

{data.get('total_stocks_negocios','?')} negócios | {data.get('total_stocks_volume','?')} acções
Capitalização Total: AOA {data.get('total_capitalizacao','?')}

""" + "\n".join(stock_lines), "metadata": {"section": "stocks_summary", "bulletin": bulletin_num, "date": date, "total_capitalizacao": data.get('total_capitalizacao')}})

    return chunks


def parse_otc_section(client, pdf_path, bulletin_num, date):
    img = render_page(pdf_path, 6)
    data = ask_vision(client, img, """Extract OTC market (Mercado de Balcão Organizado) OT-NR transactions if any.
Return ONLY this JSON:
{"otnr_otc":[{"codigo":"","data_emissao":"","data_maturidade":"","cupao":"","ytm":"","negocios":"","volume":"","cotacao_anterior":"","cotacao_actual":"","variacao":""}]}
Empty array if no transactions. Return only valid JSON.""")

    otnr_otc = data.get('otnr_otc', [])
    if not otnr_otc:
        return [{"content": f"BODIVA Boletim {bulletin_num} - {date}\nMercado de Balcão OT-NR: Não se registaram transacções.", "metadata": {"section": "otc_otnr", "bulletin": bulletin_num, "date": date}}]

    lines = [f"{b.get('codigo','?')}: cupão {b.get('cupao','?')}, YTM {b.get('ytm','?')}, preço {b.get('cotacao_actual','?')}%, var {b.get('variacao','?')}, vol {b.get('volume','?')}" for b in otnr_otc]
    return [{"content": f"BODIVA Boletim {bulletin_num} - {date}\nMERCADO DE BALCÃO ORGANIZADO - OT-NR\n\n" + "\n".join(lines), "metadata": {"section": "otc_otnr", "bulletin": bulletin_num, "date": date}}]


def parse_repos(client, pdf_path, bulletin_num, date):
    img = render_page(pdf_path, 7)
    data = ask_vision(client, img, """Extract repo market (Mercado de Operações de Reporte) data.
Return ONLY this JSON:
{"total_valor_compra":"","total_valor_recompra":"","repos":[{"colateral_codigo":"","valor_mercado":"","quantidade":"","taxa_repo":"","haircut":"","data_vencimento":"","num_dias":"","preco_compra":"","preco_recompra":""}]}
Empty array if no repos. Return only valid JSON.""")

    repos = data.get('repos', [])
    if not repos:
        return [{"content": f"BODIVA Boletim {bulletin_num} - {date}\nMercado de Operações de Reporte: Não se registaram transacções.", "metadata": {"section": "repos", "bulletin": bulletin_num, "date": date}}]

    lines = [f"Colateral: {r.get('colateral_codigo','?')} | Val. Mercado: AOA {r.get('valor_mercado','?')} | Qtd: {r.get('quantidade','?')} | Taxa: {r.get('taxa_repo','?')} | Haircut: {r.get('haircut','?')} | {r.get('num_dias','?')} dias | Venc: {r.get('data_vencimento','?')}" for r in repos]
    content = f"""BODIVA Boletim {bulletin_num} - {date}
MERCADO DE OPERAÇÕES DE REPORTE (Repo Market)

Total Compra: AOA {data.get('total_valor_compra','N/A')}
Total Recompra: AOA {data.get('total_valor_recompra','N/A')}

""" + "\n".join(lines)

    return [{"content": content, "metadata": {"section": "repos", "bulletin": bulletin_num, "date": date, "num_repos": len(repos), "total_valor": data.get('total_valor_compra')}}]


def parse_yield_curve(client, pdf_path, bulletin_num, date):
    img = render_page(pdf_path, 8)
    data = ask_vision(client, img, """Extract the complete yield curve data (Curva de Rendimentos) - both Kwanza and OT-TX curves.
Return ONLY this JSON:
{"curva_kwanza":[{"maturidade":"3M","yield_actual":"","variacao_pp":""},{"maturidade":"6M","yield_actual":"","variacao_pp":""},{"maturidade":"1Y","yield_actual":"","variacao_pp":""},{"maturidade":"2Y","yield_actual":"","variacao_pp":""},{"maturidade":"3Y","yield_actual":"","variacao_pp":""},{"maturidade":"4Y","yield_actual":"","variacao_pp":""},{"maturidade":"5Y","yield_actual":"","variacao_pp":""},{"maturidade":"6Y","yield_actual":"","variacao_pp":""},{"maturidade":"7Y","yield_actual":"","variacao_pp":""},{"maturidade":"8Y","yield_actual":"","variacao_pp":""},{"maturidade":"9Y","yield_actual":"","variacao_pp":""},{"maturidade":"10Y","yield_actual":"","variacao_pp":""}],"curva_otx":[{"maturidade":"3M","yield_actual":"","variacao_pp":""},{"maturidade":"6M","yield_actual":"","variacao_pp":""},{"maturidade":"1Y","yield_actual":"","variacao_pp":""},{"maturidade":"2Y","yield_actual":"","variacao_pp":""},{"maturidade":"3Y","yield_actual":"","variacao_pp":""},{"maturidade":"4Y","yield_actual":"","variacao_pp":""}]}
Return only valid JSON.""")

    kz = data.get('curva_kwanza', [])
    otx = data.get('curva_otx', [])
    kz_lines = [f"{p.get('maturidade','?')}: {p.get('yield_actual','?')} (var: {p.get('variacao_pp','?')} pp)" for p in kz]
    otx_lines = [f"{p.get('maturidade','?')}: {p.get('yield_actual','?')} (var: {p.get('variacao_pp','?')} pp)" for p in otx]

    content = f"""BODIVA Boletim {bulletin_num} - {date}
CURVA DE RENDIMENTOS (Yield Curve)

--- Kwanza (AOA) ---
""" + "\n".join(kz_lines) + "\n\n--- OT-TX (USD-indexada) ---\n" + "\n".join(otx_lines)

    return [{"content": content, "metadata": {"section": "yield_curve", "bulletin": bulletin_num, "date": date, "curva_kwanza": kz, "curva_otx": otx}}]


def parse_primary_market(client, pdf_path, bulletin_num, date):
    img = render_page(pdf_path, 9)
    data = ask_vision(client, img, """Extract primary market auction data and income distribution events.
Return ONLY this JSON:
{"leilao_competitivo":{"otnr":[{"maturidade":"","data_maturidade":"","taxa_cupao":"","taxa_rendimento":"","preco_subscricao":"","montante_ofertado":"","montante_colocado":""}]},"leilao_nao_competitivo":{"otnr":[{"maturidade":"","data_maturidade":"","taxa_cupao":"","taxa_rendimento":"","preco_subscricao":"","montante_ofertado":"","montante_colocado":""}]},"eventos_distribuicao":[{"emitente":"","codigo":"","moeda":"","tipo_evento":""}]}
Empty arrays if no data. Return only valid JSON.""")

    lines = []
    for item in data.get('leilao_competitivo', {}).get('otnr', []):
        o = float(item.get('montante_ofertado', 0) or 0)
        c = float(item.get('montante_colocado', 0) or 0)
        taxa = round(c/o*100, 1) if o > 0 else 0
        lines.append(f"Competitivo OT-NR {item.get('maturidade','?')}: mat {item.get('data_maturidade','?')} | cupão {item.get('taxa_cupao','?')} | ofertado AOA {item.get('montante_ofertado','?')} | colocado AOA {item.get('montante_colocado','?')} | taxa subscrição {taxa}%")

    for item in data.get('leilao_nao_competitivo', {}).get('otnr', []):
        o = float(item.get('montante_ofertado', 0) or 0)
        c = float(item.get('montante_colocado', 0) or 0)
        taxa = round(c/o*100, 1) if o > 0 else 0
        lines.append(f"Não Competitivo OT-NR {item.get('maturidade','?')}: mat {item.get('data_maturidade','?')} | cupão {item.get('taxa_cupao','?')} | ofertado AOA {item.get('montante_ofertado','?')} | colocado AOA {item.get('montante_colocado','?')} | taxa subscrição {taxa}%")

    for e in data.get('eventos_distribuicao', []):
        lines.append(f"Evento {e.get('tipo_evento','?')}: {e.get('codigo','?')} ({e.get('emitente','?')}, {e.get('moeda','?')})")

    if not lines:
        lines = ["Não se registaram emissões no Mercado Primário."]

    content = f"BODIVA Boletim {bulletin_num} - {date}\nMERCADO PRIMÁRIO - LEILÕES\n\n" + "\n".join(lines)
    return [{"content": content, "metadata": {"section": "primary_market", "bulletin": bulletin_num, "date": date}}]


def parse_bulletin(pdf_path: str, anthropic_api_key: str) -> tuple:
    """Main entry point. Returns (chunks, bulletin_num, date)."""
    client = Anthropic(api_key=anthropic_api_key)
    bulletin_num, date = extract_bulletin_number_and_date(pdf_path)
    print(f"  Boletim {bulletin_num} | {date}")

    all_chunks = []
    steps = [
        ("Quadro Resumo", parse_session_summary),
        ("Membros", parse_member_performance),
        ("OT-NR", parse_otnr_table),
        ("Acções/OT-ME/Corp", parse_stocks_and_others),
        ("OTC", parse_otc_section),
        ("Repos", parse_repos),
        ("Yield Curve", parse_yield_curve),
        ("Mercado Primário", parse_primary_market),
    ]

    for name, fn in steps:
        print(f"  Extracting {name}...")
        try:
            chunks = fn(client, pdf_path, bulletin_num, date)
            all_chunks.extend(chunks)
            print(f"    -> {len(chunks)} chunks")
        except Exception as e:
            print(f"    -> ERROR: {e}")
            all_chunks.append({"content": f"BODIVA Boletim {bulletin_num} - {date}\nSecção {name}: erro na extracção.", "metadata": {"section": name.lower().replace(' ', '_'), "bulletin": bulletin_num, "date": date, "error": str(e)}})

    print(f"  Total: {len(all_chunks)} chunks")
    return all_chunks, bulletin_num, date
