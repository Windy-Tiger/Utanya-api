"""
BODIVA Bulletin JSON to Chunks Converter (DUAL-SCHEMA)
Handles BOTH the older schema (cupao/ytm as numbers, member_performance as
dict with 'membros', repos as dict with 'operacoes', 'pontos', etc.) AND the
newer schema (cupao_yield/ytm as strings, direct lists, 'points', etc.).

Percentage/rate values are emitted as readable strings; numbers get '%'
appended (17.07 -> "17.07%"), strings pass through ("15,07%" -> "15,07%").
Never corrupts decimals.
"""

import json


def _money(v):
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
    """Percentage/rate -> readable string. Number gets % appended; string verbatim."""
    if v is None:
        return "N/A"
    if isinstance(v, (int, float)):
        return f"{v}%"
    s = str(v).strip()
    if s == "":
        return "N/A"
    return s if s.endswith("%") else f"{s}%"


def _first(d, *keys, default=None):
    """Return the first present key's value from dict d."""
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return default


def json_to_chunks(bulletin_data: dict) -> list:
    num = bulletin_data.get("bulletin_number", "?")
    date = bulletin_data.get("date", "?")
    chunks = []

    # ---------- Session Summary ----------
    ss = bulletin_data.get("session_summary", {})
    mt = _first(ss, "mercado_titulos_tesouro", "mercado_bolsa_titulos_tesouro", default={})
    multi = _first(mt, "ambiente_multilateral", default={}) if isinstance(mt, dict) else {}
    bilat = _first(mt, "ambiente_bilateral", default={}) if isinstance(mt, dict) else {}
    total_geral = _first(ss, "total_geral", "total_sessao_aoa", default=0)
    obr_priv = _first(ss, "mercado_obrigacoes_privadas", "mercado_bolsa_obrigacoes_privadas", default={})
    unip = _first(ss, "mercado_unidades_participacao", "mercado_bolsa_unidades_participacao", default={})
    acoes = _first(ss, "mercado_accoes", "mercado_bolsa_accoes", default={})
    repo_ss = _first(ss, "mercado_operacoes_reporte", default={})

    def _sec_total(x):
        if isinstance(x, dict):
            return x.get("total", 0)
        return x if x is not None else 0

    mt_total = mt.get("total", 0) if isinstance(mt, dict) else 0

    chunks.append({
        "content": f"""BODIVA Boletim {num} - {date}
QUADRO RESUMO DA SESSAO

Total Geral da Sessao (montante total negociado no mercado, volume oficial de negociacao): AOA {_money(total_geral)}

Mercado de Titulos do Tesouro: AOA {_money(mt_total)}
  Ambiente Multilateral (Bolsa): AOA {_money(multi.get('total', 0) if isinstance(multi,dict) else 0)}
    OT-NR: AOA {_money(multi.get('ot_nr', 0) if isinstance(multi,dict) else 0)}
    OT-ME: AOA {_money(multi.get('ot_me', 0) if isinstance(multi,dict) else 0)}
  Ambiente Bilateral (OTC): AOA {_money(bilat.get('total', 0) if isinstance(bilat,dict) else 0)}
    OT-NR: AOA {_money(bilat.get('ot_nr', 0) if isinstance(bilat,dict) else 0)}
    OT-ME: AOA {_money(bilat.get('ot_me', 0) if isinstance(bilat,dict) else 0)}

Mercado de Obrigacoes Privadas: AOA {_money(_sec_total(obr_priv))}
Mercado de Unidades de Participacao: AOA {_money(_sec_total(unip))}
Mercado de Accoes (MBA): AOA {_money(_sec_total(acoes))}
Mercado de Operacoes de Reporte (Repo): AOA {_money(_sec_total(repo_ss))}""",
        "metadata": {"section": "quadro_resumo", "bulletin": num, "date": date}
    })

    # ---------- Member Performance (dict-with-membros OR direct list) ----------
    mp_raw = bulletin_data.get("member_performance", [])
    if isinstance(mp_raw, dict):
        members = mp_raw.get("membros", [])
        mp_total = mp_raw.get("total", {})
    else:
        members = mp_raw
        mp_total = bulletin_data.get("member_performance_total", {})

    if members:
        lines = []
        for m in members:
            if not isinstance(m, dict):
                continue
            pct = m.get("percentagem")
            pct_str = f"{pct:.3f}%" if isinstance(pct, (int, float)) else "N/A"
            montante = _first(m, "montante_negociado", "montante_aoa", default=0)
            qtd = _first(m, "quantidade_negociada", "quantidade", default=0)
            lines.append(
                f"{m.get('membro','?')}: AOA {_money(montante)} | "
                f"{_int(qtd)} qtd | {m.get('negocios', 0)} negocios | {pct_str} quota"
            )
        tot_mont = _first(mp_total, "montante_negociado", "montante_aoa", "montante", default=0)
        tot_neg = mp_total.get("negocios", 0) if isinstance(mp_total, dict) else 0
        # Fallback: compute from members if total fields are missing/zero
        if not tot_neg:
            tot_neg = sum(m.get("negocios", 0) for m in members if isinstance(m, dict))
        if not tot_mont:
            tot_mont = sum(_first(m, "montante_negociado", "montante_aoa", default=0) for m in members if isinstance(m, dict))
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
DESEMPENHO DOS MEMBROS DE NEGOCIACAO

Soma dos montantes por membro (inclui ambas as pontas de cada negocio; NAO e o montante total da sessao): AOA {_money(tot_mont)} | {tot_neg} registos de membros

""" + "\n".join(lines),
            "metadata": {"section": "desempenho_membros", "bulletin": num, "date": date}
        })

    # ---------- helper to render a bond list (works for both schemas) ----------
    def bond_summary_line(b):
        cupao = _first(b, "cupao_yield", "cupao")
        ytm = _first(b, "ytm")
        return (f"{b.get('codigo','?')}: cupao {_pct(cupao)}, YTM {_pct(ytm)}, "
                f"cotacao {b.get('cotacao_actual','?')}, var {b.get('variacao_pct','?')}%, "
                f"vol {_int(_first(b,'volume_total','volume',default=0))}")

    def bond_detail(b, label):
        cupao = _first(b, "cupao_yield", "cupao")
        ytm = _first(b, "ytm")
        negs = _first(b, "negocios_realizados", "negocios", default=0)
        return f"""BODIVA Boletim {num} - {date}
{label}: {b.get('codigo','?')}

Codigo: {b.get('codigo','?')}
Data de Emissao: {b.get('data_emissao', 'N/A')}
Data de Maturidade: {b.get('data_maturidade', 'N/A')}
Taxa de Cupao: {_pct(cupao)} ao ano
YTM (Yield to Maturity): {_pct(ytm)}
Negocios Realizados: {negs}
Volume Total: {_int(_first(b,'volume_total','volume',default=0))} unidades
Preco de Abertura: {b.get('abertura', 'N/A')}
Preco Maximo: {b.get('maximo', 'N/A')}
Preco Minimo: {b.get('minimo', 'N/A')}
Cotacao Anterior: {b.get('cotacao_anterior', 'N/A')}
Cotacao Actual: {b.get('cotacao_actual', 'N/A')}
Variacao: {b.get('variacao_pct', 'N/A')}%"""

    # ---------- OT-NR Exchange ----------
    bonds = bulletin_data.get("otnr_exchange", [])
    if isinstance(bonds, dict):
        bonds = bonds.get("bonds", [])
    otnr_total = bulletin_data.get("otnr_exchange_total", {})
    if bonds:
        tot_neg = _first(otnr_total, "negocios_realizados", "negocios", default=0)
        tot_vol = _first(otnr_total, "volume_total", default=0)
        if not tot_neg:
            tot_neg = sum(_first(b, "negocios_realizados", "negocios", default=0) for b in bonds)
        if not tot_vol:
            tot_vol = sum(_first(b, "volume_total", "volume", default=0) for b in bonds)
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
OT-NR MERCADO DE BOLSA - RESUMO ({len(bonds)} instrumentos)
Total: {tot_neg} negocios | {_int(tot_vol)} unidades

""" + "\n".join(bond_summary_line(b) for b in bonds),
            "metadata": {"section": "otnr_summary", "bulletin": num, "date": date, "total_bonds": len(bonds)}
        })
        for b in bonds:
            chunks.append({
                "content": bond_detail(b, "OT-NR (Obrigacao do Tesouro Nao Reajustavel)"),
                "metadata": {
                    "section": "otnr_bond", "instrument_code": b.get("codigo", "unknown"),
                    "bulletin": num, "date": date,
                    "cupao": _first(b, "cupao_yield", "cupao"), "ytm": b.get("ytm"),
                    "variacao": b.get("variacao_pct"), "cotacao": b.get("cotacao_actual"),
                    "maturidade": b.get("data_maturidade")
                }
            })

    # ---------- OT-ME Exchange ----------
    otme = bulletin_data.get("otme_exchange", [])
    if isinstance(otme, dict):
        otme = otme.get("bonds", [])
    if otme:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nOT-ME MERCADO DE BOLSA (USD)\n\n" +
                       "\n".join(bond_summary_line(b) for b in otme),
            "metadata": {"section": "otme_exchange", "bulletin": num, "date": date}
        })
    else:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nOT-ME Mercado de Bolsa: Nao se registaram transaccoes nesta sessao.",
            "metadata": {"section": "otme_exchange", "bulletin": num, "date": date}
        })

    # ---------- Corporate Bonds ----------
    corp = bulletin_data.get("corporate_bonds", [])
    if isinstance(corp, dict):
        corp = corp.get("bonds", [])
    corp_total = bulletin_data.get("corporate_bonds_total", {}) or {}
    if corp:
        tot_neg = _first(corp_total, "negocios_realizados", "negocios", default=sum(_first(b,'negocios_realizados','negocios',default=0) for b in corp))
        tot_vol = _first(corp_total, "volume_total", default=sum(_first(b,'volume_total','volume',default=0) for b in corp))
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE OBRIGACOES PRIVADAS (Corporate Bonds)
Total: {tot_neg} negocios | {_int(tot_vol)} unidades

""" + "\n".join(bond_summary_line(b) for b in corp),
            "metadata": {"section": "corporate_bonds", "bulletin": num, "date": date}
        })

    # ---------- Stocks ----------
    stocks = bulletin_data.get("stocks", [])
    if isinstance(stocks, dict):
        stocks = stocks.get("companies", [])
    stocks_total = bulletin_data.get("stocks_total", {}) or {}
    if stocks:
        stock_lines = [
            f"{c.get('codigo','?')}: cotacao AOA {_money(c.get('cotacao_actual', 0))} | "
            f"var {c.get('variacao_pct', 0)}% | {_first(c,'negocios_realizados','negocios',default=0)} negocios | "
            f"{_int(_first(c,'volume_total','volume',default=0))} accoes | "
            f"Cap AOA {_money(c.get('capitalizacao_bolsista', 0))}"
            for c in stocks
        ]
        tot_neg = _first(stocks_total, "negocios_realizados", "negocios", default=0)
        tot_vol = _first(stocks_total, "volume_total", default=0)
        tot_cap = _first(stocks_total, "capitalizacao_bolsista_total", "capitalizacao_total", default=0)
        if not tot_neg:
            tot_neg = sum(_first(c, "negocios_realizados", "negocios", default=0) for c in stocks)
        if not tot_vol:
            tot_vol = sum(_first(c, "volume_total", "volume", default=0) for c in stocks)
        if not tot_cap:
            tot_cap = sum(c.get("capitalizacao_bolsista", 0) for c in stocks if isinstance(c, dict))
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE ACCOES - RESUMO DA SESSAO

Total: {tot_neg} negocios | {_int(tot_vol)} accoes
Capitalizacao Bolsista Total: AOA {_money(tot_cap)}

""" + "\n".join(stock_lines),
            "metadata": {"section": "stocks_summary", "bulletin": num, "date": date,
                         "total_capitalizacao": tot_cap}
        })
        for c in stocks:
            chunks.append({
                "content": f"""BODIVA Boletim {num} - {date}
ACCAO: {c.get('codigo','?')}

Codigo: {c.get('codigo','?')}
Empresa: {_first(c,'empresa','nome',default='N/A')}
Data de Emissao (admissao): {c.get('data_emissao', 'N/A')}
Negocios: {_first(c,'negocios_realizados','negocios',default=0)}
Volume (accoes transaccionadas): {_int(_first(c,'volume_total','volume',default=0))}
Preco de Abertura: AOA {_money(c.get('abertura', 0))}
Preco Maximo: AOA {_money(c.get('maximo', 0))}
Preco Minimo: AOA {_money(c.get('minimo', 0))}
Cotacao Anterior: AOA {_money(c.get('cotacao_anterior', 0))}
Cotacao Actual: AOA {_money(c.get('cotacao_actual', 0))}
Variacao: {c.get('variacao_pct', 0)}%
Capitalizacao Bolsista: AOA {_money(c.get('capitalizacao_bolsista', 0))}""",
                "metadata": {
                    "section": "stock", "instrument_code": c.get("codigo", "unknown"),
                    "bulletin": num, "date": date,
                    "variacao": c.get("variacao_pct"), "preco": c.get("cotacao_actual"),
                    "capitalizacao": c.get("capitalizacao_bolsista")
                }
            })

    # ---------- OTC OT-NR ----------
    otc = bulletin_data.get("otc_otnr", [])
    if isinstance(otc, dict):
        otc = otc.get("bonds", [])
    otc_total = bulletin_data.get("otc_otnr_total", {}) or {}
    if otc:
        tot_neg = _first(otc_total, "negocios_realizados", "negocios", default=0)
        tot_vol = _first(otc_total, "volume_total", default=0)
        if not tot_neg:
            tot_neg = sum(_first(b, "negocios_realizados", "negocios", default=0) for b in otc)
        if not tot_vol:
            tot_vol = sum(_first(b, "volume_total", "volume", default=0) for b in otc)
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE BALCAO ORGANIZADO - OT-NR (OTC)
Total: {tot_neg} negocios | {_int(tot_vol)} unidades

""" + "\n".join(bond_summary_line(b) for b in otc),
            "metadata": {"section": "otc_otnr", "bulletin": num, "date": date}
        })
    else:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nMercado de Balcao OT-NR: Nao se registaram transaccoes nesta sessao.",
            "metadata": {"section": "otc_otnr", "bulletin": num, "date": date}
        })

    # ---------- Repos (dict-with-operacoes OR direct list) ----------
    repos_raw = bulletin_data.get("repos", [])
    if isinstance(repos_raw, dict):
        repo_ops = repos_raw.get("operacoes", [])
        repos_total = repos_raw.get("total", {}) or {}
    else:
        repo_ops = repos_raw
        repos_total = bulletin_data.get("repos_total", {}) or {}
    if repo_ops:
        lines = [
            f"Colateral: {r.get('codigo','?')} ({r.get('tipologia','?')}) | "
            f"Valor Mercado: AOA {_money(r.get('valor_mercado', 0))} | "
            f"Qtd: {_int(_first(r,'qtde','quantidade',default=0))} | "
            f"Taxa Repo: {_pct(r.get('taxa_repo'))} | Haircut: {_pct(r.get('haircut'))} | "
            f"Prazo: {_first(r,'numero_dias','num_dias',default='?')} dias | "
            f"Vencimento: {r.get('data_vencimento','?')}"
            for r in repo_ops
        ]
        tv_compra = _first(repos_total, "preco_compra", "total_valor_compra", "valor_mercado", default=0)
        tv_recompra = _first(repos_total, "preco_recompra", "total_valor_recompra", default=0)
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE OPERACOES DE REPORTE (Repo Market)

Total Valor de Compra: AOA {_money(tv_compra)}
Total Valor de Recompra: AOA {_money(tv_recompra)}
Numero de Operacoes: {len(repo_ops)}

Operacoes:
""" + "\n".join(lines),
            "metadata": {"section": "repos", "bulletin": num, "date": date, "num_repos": len(repo_ops)}
        })
    else:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nMercado de Operacoes de Reporte: Nao se registaram operacoes nesta sessao.",
            "metadata": {"section": "repos", "bulletin": num, "date": date}
        })

    # ---------- Yield Curve Kz (list OR dict-with-points/pontos) ----------
    yc_kz_data = bulletin_data.get("yield_curve_kz", {})
    if isinstance(yc_kz_data, list):
        yc_kz = yc_kz_data
        yc_kz_ref = ""
    elif isinstance(yc_kz_data, dict):
        yc_kz = _first(yc_kz_data, "points", "pontos", default=[])
        yc_kz_ref = yc_kz_data.get("data_referencia", "N/A")
    else:
        yc_kz = []
        yc_kz_ref = ""
    if yc_kz:
        lines = [
            f"{p.get('maturidade','?')}: {_first(p,'tx_rend_actual','taxa_actual','yield',default='?')}% "
            f"(ontem: {_first(p,'tx_rend_ontem','taxa_ontem','yield_ontem',default='?')}%)"
            for p in yc_kz
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
CURVA DE RENDIMENTOS KWANZA (AOA) - Data Referencia: {yc_kz_ref}

""" + "\n".join(lines),
            "metadata": {"section": "yield_curve_kz", "bulletin": num, "date": date}
        })

    # ---------- Yield Curve OT-TX (list OR dict-with-points/pontos) ----------
    yc_otx_data = bulletin_data.get("yield_curve_otx", {})
    if isinstance(yc_otx_data, list):
        yc_otx = yc_otx_data
        yc_otx_ref = ""
    elif isinstance(yc_otx_data, dict):
        yc_otx = _first(yc_otx_data, "points", "pontos", default=[])
        yc_otx_ref = yc_otx_data.get("data_referencia", "N/A")
    else:
        yc_otx = []
        yc_otx_ref = ""
    if yc_otx:
        lines = [f"{p.get('maturidade','?')}: {_first(p,'tx_rend_actual','taxa_actual','yield',default='?')}%" for p in yc_otx]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
CURVA DE RENDIMENTOS OT-TX (USD-indexada) - Data Referencia: {yc_otx_ref}

""" + "\n".join(lines),
            "metadata": {"section": "yield_curve_otx", "bulletin": num, "date": date}
        })

    # ---------- Primary Market ----------
    pm = bulletin_data.get("primary_market", {})
    comp_otnr = pm.get("leilao_competitivo", {}).get("otnr", []) if isinstance(pm.get("leilao_competitivo"), dict) else []
    ncomp_otnr = pm.get("leilao_nao_competitivo", {}).get("otnr", []) if isinstance(pm.get("leilao_nao_competitivo"), dict) else []
    combined = pm.get("leilao_otnr_combined_total", {}) or {}
    eventos = bulletin_data.get("eventos_distribuicao", [])

    pm_lines = []
    for item in (comp_otnr or []):
        if isinstance(item, dict):
            pm_lines.append(
                f"Leilao Competitivo OT-NR {item.get('maturidade','?')}: "
                f"cupao {_pct(item.get('taxa_cupao'))} | yield {_pct(item.get('taxa_rendimento'))} | "
                f"ofertado AOA {_int(item.get('montante_ofertado', 0))} | colocado AOA {_int(item.get('montante_colocado', 0))}"
            )
    for item in (ncomp_otnr or []):
        if isinstance(item, dict):
            pm_lines.append(
                f"Leilao Nao Competitivo OT-NR {item.get('maturidade','?')}: "
                f"cupao {_pct(item.get('taxa_cupao'))} | yield {_pct(item.get('taxa_rendimento'))} | "
                f"ofertado AOA {_int(item.get('montante_ofertado', 0))} | colocado AOA {_int(item.get('montante_colocado', 0))}"
            )
    for e in (eventos or []):
        if isinstance(e, dict):
            pm_lines.append(
                f"Evento de Distribuicao: {e.get('tipo_evento','?')} | "
                f"codigo {_first(e,'codigo_negociacao','codigo',default='?')} | "
                f"emitente {e.get('emitente','?')} | moeda {e.get('moeda','?')}"
            )
    if not pm_lines:
        pm_lines = ["Nao se registaram emissoes no Mercado Primario nesta sessao."]

    total_ofertado = combined.get("montante_ofertado", 0)
    total_colocado = combined.get("montante_colocado", 0)
    taxa_global = combined.get("subscription_rate_pct", 0)

    chunks.append({
        "content": f"""BODIVA Boletim {num} - {date}
MERCADO PRIMARIO - LEILOES DE TITULOS DO TESOURO

""" + "\n".join(pm_lines),
        "metadata": {"section": "primary_market", "bulletin": num, "date": date,
                     "total_ofertado": total_ofertado, "total_colocado": total_colocado,
                     "taxa_subscricao": taxa_global}
    })

    return chunks
