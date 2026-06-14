"""
BODIVA Bulletin JSON to Chunks Converter (CORRECTED SCHEMA)
Converts a pre-extracted structured bulletin JSON into RAG chunks.

KEY PRINCIPLE: percentage/rate fields in the JSON are already clean strings
in Portuguese format (e.g. "15,07%", "16,75%"). They are inserted VERBATIM.
We never call float()/replace() on them, so "15,07%" stays "15,07%" and is
never corrupted into 1507. Monetary/numeric fields that arrive as real
numbers are formatted with thousands separators for readability.
"""

import json


def _money(v):
    """Format a numeric value as a thousands-separated amount. Safe on None."""
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v) if v is not None else "0,00"


def _int(v):
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v) if v is not None else "0"


def _pct(v):
    """Percentage/rate fields are already strings like '15,07%'. Return verbatim.
    If a bare number sneaks in, append % without reformatting the digits."""
    if v is None:
        return "N/A"
    s = str(v).strip()
    if s == "":
        return "N/A"
    return s if s.endswith("%") else f"{s}%"


def json_to_chunks(bulletin_data: dict) -> list:
    num = bulletin_data.get("bulletin_number", "?")
    date = bulletin_data.get("date", "?")
    chunks = []

    # --- Session Summary ---
    ss = bulletin_data.get("session_summary", {})
    mt = ss.get("mercado_titulos_tesouro", {})
    multi = mt.get("ambiente_multilateral", {})
    bilat = mt.get("ambiente_bilateral", {})
    obr_priv = ss.get("mercado_obrigacoes_privadas", {})
    unip = ss.get("mercado_unidades_participacao", {})
    acoes = ss.get("mercado_accoes", {})
    repo_ss = ss.get("mercado_operacoes_reporte", {})

    def _sec_total(x):
        # some summary sections are dicts with 'total', some are bare numbers
        if isinstance(x, dict):
            return x.get("total", 0)
        return x

    chunks.append({
        "content": f"""BODIVA Boletim {num} - {date}
QUADRO RESUMO DA SESSAO

Total Geral Negociado: AOA {_money(ss.get('total_geral', 0))}

Mercado de Titulos do Tesouro: AOA {_money(mt.get('total', 0))}
  Ambiente Multilateral (Bolsa): AOA {_money(multi.get('total', 0))}
    OT-NR: AOA {_money(multi.get('ot_nr', 0))}
    OT-ME: AOA {_money(multi.get('ot_me', 0))}
    OT-TX: AOA {_money(multi.get('ot_tx', 0))}
  Ambiente Bilateral (OTC): AOA {_money(bilat.get('total', 0))}
    OT-NR: AOA {_money(bilat.get('ot_nr', 0))}
    OT-ME: AOA {_money(bilat.get('ot_me', 0))}

Mercado de Obrigacoes Privadas: AOA {_money(_sec_total(obr_priv))}
Mercado de Unidades de Participacao: AOA {_money(_sec_total(unip))}
Mercado de Accoes (MBA): AOA {_money(_sec_total(acoes))}
Mercado de Operacoes de Reporte (Repo): AOA {_money(_sec_total(repo_ss))}""",
        "metadata": {"section": "quadro_resumo", "bulletin": num, "date": date}
    })

    # --- Member Performance (direct list) ---
    members = bulletin_data.get("member_performance", [])
    mp_total = bulletin_data.get("member_performance_total", {})
    if members:
        lines = []
        for m in members:
            pct = m.get("percentagem")
            pct_str = f"{pct:.3f}%" if isinstance(pct, (int, float)) else "N/A"
            lines.append(
                f"{m.get('membro','?')}: "
                f"AOA {_money(m.get('montante_negociado', 0))} | "
                f"{_int(m.get('quantidade_negociada', 0))} qtd | "
                f"{m.get('negocios', 0)} negocios | "
                f"{pct_str} quota de mercado"
            )
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
DESEMPENHO DOS MEMBROS DE NEGOCIACAO

Total do Mercado: AOA {_money(mp_total.get('montante_negociado', 0))} | """
            f"""{mp_total.get('negocios', 0)} negocios | {_int(mp_total.get('quantidade_negociada', 0))} qtd

""" + "\n".join(lines),
            "metadata": {"section": "desempenho_membros", "bulletin": num, "date": date}
        })

    # --- OT-NR Exchange (direct list): summary + per-bond ---
    bonds = bulletin_data.get("otnr_exchange", [])
    otnr_total = bulletin_data.get("otnr_exchange_total", {})
    if bonds:
        summary_lines = [
            f"{b.get('codigo','?')}: cupao {_pct(b.get('cupao_yield'))}, "
            f"YTM {_pct(b.get('ytm'))}, "
            f"cotacao {b.get('cotacao_actual','?')}, "
            f"var {b.get('variacao_pct','?')}%, "
            f"vol {_int(b.get('volume_total', 0))}"
            for b in bonds
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
OT-NR MERCADO DE BOLSA - RESUMO ({len(bonds)} instrumentos)
Total: {otnr_total.get('negocios_realizados', 0)} negocios | {_int(otnr_total.get('volume_total', 0))} unidades

""" + "\n".join(summary_lines),
            "metadata": {"section": "otnr_summary", "bulletin": num, "date": date, "total_bonds": len(bonds)}
        })

        for b in bonds:
            codigo = b.get("codigo", "unknown")
            chunks.append({
                "content": f"""BODIVA Boletim {num} - {date}
OT-NR (Obrigacao do Tesouro Nao Reajustavel): {codigo}

Codigo: {codigo}
Data de Emissao: {b.get('data_emissao', 'N/A')}
Data de Maturidade: {b.get('data_maturidade', 'N/A')}
Taxa de Cupao: {_pct(b.get('cupao_yield'))} ao ano
YTM (Yield to Maturity): {_pct(b.get('ytm'))}
Negocios Realizados: {b.get('negocios_realizados', 0)}
Volume Total: {_int(b.get('volume_total', 0))} unidades (nominal Kz 1.000 cada)
Preco de Abertura: {b.get('abertura', 'N/A')}
Preco Maximo: {b.get('maximo', 'N/A')}
Preco Minimo: {b.get('minimo', 'N/A')}
Cotacao Anterior: {b.get('cotacao_anterior', 'N/A')}
Cotacao Actual: {b.get('cotacao_actual', 'N/A')}
Variacao: {b.get('variacao_pct', 'N/A')}%""",
                "metadata": {
                    "section": "otnr_bond", "instrument_code": codigo,
                    "bulletin": num, "date": date,
                    "cupao": b.get("cupao_yield"), "ytm": b.get("ytm"),
                    "variacao": b.get("variacao_pct"), "cotacao": b.get("cotacao_actual"),
                    "maturidade": b.get("data_maturidade")
                }
            })

    # --- OT-ME Exchange (direct list) ---
    otme_bonds = bulletin_data.get("otme_exchange", [])
    if otme_bonds:
        lines = [
            f"{b.get('codigo','?')}: cupao {_pct(b.get('cupao_yield'))}, "
            f"YTM {_pct(b.get('ytm'))}, cotacao {b.get('cotacao_actual','?')}, "
            f"var {b.get('variacao_pct','?')}%"
            for b in otme_bonds
        ]
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nOT-ME MERCADO DE BOLSA (USD)\n\n" + "\n".join(lines),
            "metadata": {"section": "otme_exchange", "bulletin": num, "date": date}
        })
    else:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nOT-ME Mercado de Bolsa: Nao se registaram transaccoes nesta sessao.",
            "metadata": {"section": "otme_exchange", "bulletin": num, "date": date}
        })

    # --- Corporate Bonds (direct list) ---
    corp_bonds = bulletin_data.get("corporate_bonds", [])
    corp_total = bulletin_data.get("corporate_bonds_total", {})
    if corp_bonds:
        lines = [
            f"{b.get('codigo','?')}: cupao {_pct(b.get('cupao_yield'))}, "
            f"YTM {_pct(b.get('ytm'))}, cotacao {b.get('cotacao_actual','?')}, "
            f"var {b.get('variacao_pct','?')}%, vol {_int(b.get('volume_total', 0))}"
            for b in corp_bonds
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE OBRIGACOES PRIVADAS (Corporate Bonds)
Total: {corp_total.get('negocios_realizados', 0)} negocios | {_int(corp_total.get('volume_total', 0))} unidades

""" + "\n".join(lines),
            "metadata": {"section": "corporate_bonds", "bulletin": num, "date": date}
        })

    # --- Stocks (direct list): summary + per-company ---
    companies = bulletin_data.get("stocks", [])
    stocks_total = bulletin_data.get("stocks_total", {})
    if companies:
        stock_lines = [
            f"{c.get('codigo','?')}: "
            f"cotacao AOA {_money(c.get('cotacao_actual', 0))} | "
            f"var {c.get('variacao_pct', 0)}% | "
            f"{c.get('negocios_realizados', 0)} negocios | "
            f"{_int(c.get('volume_total', 0))} accoes | "
            f"Cap AOA {_money(c.get('capitalizacao_bolsista', 0))}"
            for c in companies
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE ACCOES - RESUMO DA SESSAO

Total: {stocks_total.get('negocios_realizados', 0)} negocios | {_int(stocks_total.get('volume_total', 0))} accoes
Capitalizacao Bolsista Total: AOA {_money(stocks_total.get('capitalizacao_bolsista_total', 0))}

""" + "\n".join(stock_lines),
            "metadata": {
                "section": "stocks_summary", "bulletin": num, "date": date,
                "total_capitalizacao": stocks_total.get("capitalizacao_bolsista_total")
            }
        })

        for c in companies:
            codigo = c.get("codigo", "unknown")
            chunks.append({
                "content": f"""BODIVA Boletim {num} - {date}
ACCAO: {codigo}

Codigo: {codigo}
Data de Emissao (admissao): {c.get('data_emissao', 'N/A')}
Negocios: {c.get('negocios_realizados', 0)}
Volume (accoes transaccionadas): {_int(c.get('volume_total', 0))}
Preco de Abertura: AOA {_money(c.get('abertura', 0))}
Preco Maximo: AOA {_money(c.get('maximo', 0))}
Preco Minimo: AOA {_money(c.get('minimo', 0))}
Cotacao Anterior: AOA {_money(c.get('cotacao_anterior', 0))}
Cotacao Actual: AOA {_money(c.get('cotacao_actual', 0))}
Variacao: {c.get('variacao_pct', 0)}%
Capitalizacao Bolsista: AOA {_money(c.get('capitalizacao_bolsista', 0))}""",
                "metadata": {
                    "section": "stock", "instrument_code": codigo,
                    "bulletin": num, "date": date,
                    "variacao": c.get("variacao_pct"),
                    "preco": c.get("cotacao_actual"),
                    "capitalizacao": c.get("capitalizacao_bolsista")
                }
            })

    # --- OTC OT-NR (direct list) ---
    otc_bonds = bulletin_data.get("otc_otnr", [])
    otc_total = bulletin_data.get("otc_otnr_total", {})
    if otc_bonds:
        lines = [
            f"{b.get('codigo','?')}: cupao {_pct(b.get('cupao_yield'))}, "
            f"YTM {_pct(b.get('ytm'))}, cotacao {b.get('cotacao_actual','?')}, "
            f"var {b.get('variacao_pct','?')}%, vol {_int(b.get('volume_total', 0))}"
            for b in otc_bonds
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE BALCAO ORGANIZADO - OT-NR (OTC)
Total: {otc_total.get('negocios_realizados', 0)} negocios | {_int(otc_total.get('volume_total', 0))} unidades

""" + "\n".join(lines),
            "metadata": {"section": "otc_otnr", "bulletin": num, "date": date}
        })
    else:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nMercado de Balcao OT-NR: Nao se registaram transaccoes nesta sessao.",
            "metadata": {"section": "otc_otnr", "bulletin": num, "date": date}
        })

    # --- Repos (direct list) ---
    repo_ops = bulletin_data.get("repos", [])
    repos_total = bulletin_data.get("repos_total", {})
    if repo_ops:
        lines = [
            f"Colateral: {r.get('codigo','?')} ({r.get('tipologia','?')}) | "
            f"Valor Mercado: AOA {_money(r.get('valor_mercado', 0))} | "
            f"Qtd: {_int(r.get('qtde', 0))} | "
            f"Taxa Repo: {_pct(r.get('taxa_repo'))} | "
            f"Haircut: {_pct(r.get('haircut'))} | "
            f"Prazo: {r.get('numero_dias','?')} dias | "
            f"Vencimento: {r.get('data_vencimento','?')} | "
            f"Preco Compra: AOA {_money(r.get('preco_compra', 0))} | "
            f"Preco Recompra: AOA {_money(r.get('preco_recompra', 0))}"
            for r in repo_ops
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE OPERACOES DE REPORTE (Repo Market)

Total Valor de Compra: AOA {_money(repos_total.get('preco_compra', 0))}
Total Valor de Recompra: AOA {_money(repos_total.get('preco_recompra', 0))}
Total Quantidade: {_int(repos_total.get('qtde', 0))} unidades
Numero de Operacoes: {len(repo_ops)}

Operacoes:
""" + "\n".join(lines),
            "metadata": {
                "section": "repos", "bulletin": num, "date": date,
                "num_repos": len(repo_ops),
                "total_valor": repos_total.get("preco_compra")
            }
        })
    else:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nMercado de Operacoes de Reporte: Nao se registaram operacoes nesta sessao.",
            "metadata": {"section": "repos", "bulletin": num, "date": date}
        })

    # --- Yield Curve Kz ---
    yc_kz_data = bulletin_data.get("yield_curve_kz", {})
    yc_kz = yc_kz_data.get("points", []) if isinstance(yc_kz_data, dict) else []
    if yc_kz:
        lines = [
            f"{p.get('maturidade','?')}: {p.get('tx_rend_actual','?')}% "
            f"(ontem: {p.get('tx_rend_ontem','?')}%, var: {p.get('variacao_pp','?')} pp)"
            for p in yc_kz
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
CURVA DE RENDIMENTOS KWANZA (AOA) - Data Referencia: {yc_kz_data.get('data_referencia', 'N/A')}

""" + "\n".join(lines),
            "metadata": {"section": "yield_curve_kz", "bulletin": num, "date": date}
        })

    # --- Yield Curve OT-TX ---
    yc_otx_data = bulletin_data.get("yield_curve_otx", {})
    yc_otx = yc_otx_data.get("points", []) if isinstance(yc_otx_data, dict) else []
    if yc_otx:
        lines = [
            f"{p.get('maturidade','?')}: {p.get('tx_rend_actual','?')}% "
            f"(var: {p.get('variacao_pp','?')} pp)"
            for p in yc_otx
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
CURVA DE RENDIMENTOS OT-TX (USD-indexada) - Data Referencia: {yc_otx_data.get('data_referencia', 'N/A')}

""" + "\n".join(lines),
            "metadata": {"section": "yield_curve_otx", "bulletin": num, "date": date}
        })

    # --- Primary Market ---
    pm = bulletin_data.get("primary_market", {})
    comp_otnr = pm.get("leilao_competitivo", {}).get("otnr", [])
    ncomp_otnr = pm.get("leilao_nao_competitivo", {}).get("otnr", [])
    combined = pm.get("leilao_otnr_combined_total", {})
    eventos = bulletin_data.get("eventos_distribuicao", [])

    pm_lines = []
    for item in comp_otnr:
        if isinstance(item, dict):
            pm_lines.append(
                f"Leilao Competitivo OT-NR {item.get('maturidade','?')}: "
                f"maturidade {item.get('data_maturidade','?')} | "
                f"cupao {_pct(item.get('taxa_cupao'))} | "
                f"yield {_pct(item.get('taxa_rendimento'))} | "
                f"ofertado AOA {_int(item.get('montante_ofertado', 0))} | "
                f"colocado AOA {_int(item.get('montante_colocado', 0))}"
            )
    for item in ncomp_otnr:
        if isinstance(item, dict):
            pm_lines.append(
                f"Leilao Nao Competitivo OT-NR {item.get('maturidade','?')}: "
                f"maturidade {item.get('data_maturidade','?')} | "
                f"cupao {_pct(item.get('taxa_cupao'))} | "
                f"yield {_pct(item.get('taxa_rendimento'))} | "
                f"ofertado AOA {_int(item.get('montante_ofertado', 0))} | "
                f"colocado AOA {_int(item.get('montante_colocado', 0))}"
            )
    for e in eventos:
        pm_lines.append(
            f"Evento de Distribuicao: {e.get('tipo_evento','?')} | "
            f"codigo {e.get('codigo_negociacao','?')} | "
            f"emitente {e.get('emitente','?')} | "
            f"moeda {e.get('moeda','?')} | "
            f"ISIN {e.get('isin','?')}"
        )

    if not pm_lines:
        pm_lines = ["Nao se registaram emissoes no Mercado Primario nesta sessao."]

    total_ofertado = combined.get("montante_ofertado", 0)
    total_colocado = combined.get("montante_colocado", 0)
    taxa_global = combined.get("subscription_rate_pct", 0)
    try:
        taxa_global_f = float(taxa_global)
    except (TypeError, ValueError):
        taxa_global_f = 0.0

    sub_note = ("O leilao ficou SUBSUBSCRITO - o governo nao captou o total pretendido."
                if taxa_global_f < 100 else "Leilao totalmente subscrito.")

    chunks.append({
        "content": f"""BODIVA Boletim {num} - {date}
MERCADO PRIMARIO - LEILOES DE TITULOS DO TESOURO

O governo angolano (UGD/Tesouro) ofereceu AOA {_int(total_ofertado)} no total.
Colocou AOA {_int(total_colocado)} - taxa de subscricao global: {taxa_global}%.
{sub_note}

""" + "\n".join(pm_lines),
        "metadata": {
            "section": "primary_market", "bulletin": num, "date": date,
            "total_ofertado": total_ofertado, "total_colocado": total_colocado,
            "taxa_subscricao": taxa_global
        }
    })

    return chunks
