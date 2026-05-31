"""
BODIVA Bulletin JSON to Chunks Converter
Converts a pre-extracted structured bulletin JSON file into RAG chunks.
This approach gives 100% accurate data because Claude reads the PDF natively
in the conversation and produces the JSON, which this module then chunks.
"""

import json


def json_to_chunks(bulletin_data: dict) -> list:
    """Convert a structured bulletin JSON into a list of precise RAG chunks."""
    
    num = bulletin_data.get("bulletin_number", "?")
    date = bulletin_data.get("date", "?")
    chunks = []

    # --- Session Summary ---
    ss = bulletin_data.get("session_summary", {})
    mt = ss.get("mercado_titulos_tesouro", {})
    multi = mt.get("multilateral", {})
    bilat = mt.get("bilateral", {})

    chunks.append({
        "content": f"""BODIVA Boletim {num} - {date}
QUADRO RESUMO DA SESSÃO

Total Negociado: AOA {ss.get('total_negociado', 0):,.2f}

Mercado de Títulos do Tesouro: AOA {mt.get('total', 0):,.2f}
  Multilateral (Bolsa): AOA {multi.get('total', 0):,.2f}
    OT-NR bolsa: AOA {multi.get('ot_nr', 0):,.2f}
    OT-ME bolsa: AOA {multi.get('ot_me', 0):,.2f}
    Bilhetes Tesouro: AOA {multi.get('bt', 0):,.2f}
  Bilateral (OTC): AOA {bilat.get('total', 0):,.2f}
    OT-NR OTC: AOA {bilat.get('ot_nr', 0):,.2f}
    OT-ME OTC: AOA {bilat.get('ot_me', 0):,.2f}

Mercado de Obrigações Privadas: AOA {ss.get('mercado_obrigacoes_privadas', 0):,.2f}
Mercado de Unidades de Participação: AOA {ss.get('mercado_uniparticipacao', 0):,.2f}
Mercado de Acções (MBA): AOA {ss.get('mercado_acoes', 0):,.2f}
Mercado de Operações de Reporte (Repo): AOA {ss.get('mercado_operacoes_reporte', 0):,.2f}""",
        "metadata": {
            "section": "quadro_resumo",
            "bulletin": num,
            "date": date
        }
    })

    # --- Member Performance ---
    mp = bulletin_data.get("member_performance", {})
    members = mp.get("members", [])
    if members:
        lines = []
        for m in members:
            lines.append(
                f"{m.get('code','?')} ({m.get('name','?')}): "
                f"AOA {m.get('montante', 0):,.2f} | "
                f"{m.get('negocios', 0)} negócios | "
                f"{m.get('percentagem', 0):.3f}% quota de mercado | "
                f"Vendas: AOA {m.get('vendas', 0):,.2f} | "
                f"Compras: AOA {m.get('compras', 0):,.2f} | "
                f"Internos: AOA {m.get('internos', 0):,.2f}"
            )

        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
DESEMPENHO DOS MEMBROS DE NEGOCIAÇÃO

Total do Mercado: AOA {mp.get('total_montante', 0):,.2f} | {mp.get('total_negocios', 0)} negócios

""" + "\n".join(lines),
            "metadata": {
                "section": "desempenho_membros",
                "bulletin": num,
                "date": date
            }
        })

    # --- OT-NR Table: summary chunk + one chunk per bond ---
    otnr = bulletin_data.get("otnr_exchange", {})
    bonds = otnr.get("bonds", [])

    if bonds:
        summary_lines = [
            f"{b.get('codigo','?')}: cupão {b.get('cupao','?')}%, "
            f"YTM {b.get('ytm','?')}%, "
            f"preço {b.get('cotacao_actual','?')}%, "
            f"var {b.get('variacao','?')}%, "
            f"vol {b.get('volume', 0):,} unidades"
            for b in bonds
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
OT-NR MERCADO DE BOLSA - RESUMO ({len(bonds)} instrumentos)
Total: {otnr.get('total_negocios', 0)} negócios | {otnr.get('total_volume', 0):,} unidades

""" + "\n".join(summary_lines),
            "metadata": {
                "section": "otnr_summary",
                "bulletin": num,
                "date": date,
                "total_bonds": len(bonds)
            }
        })

        for bond in bonds:
            codigo = bond.get('codigo', 'unknown')
            chunks.append({
                "content": f"""BODIVA Boletim {num} - {date}
OT-NR (Obrigação do Tesouro Não Reajustável): {codigo}

Código: {codigo}
Data de Emissão: {bond.get('emissao', 'N/A')}
Data de Maturidade: {bond.get('maturidade', 'N/A')}
Taxa de Cupão: {bond.get('cupao', 'N/A')}% ao ano
YTM (Yield to Maturity): {bond.get('ytm', 'N/A')}%
Negócios Realizados: {bond.get('negocios', 0)}
Volume Total: {bond.get('volume', 0):,} unidades (nominal Kz 1.000 cada)
Preço de Abertura: {bond.get('abertura', 'N/A')}%
Preço Máximo: {bond.get('maximo', 'N/A')}%
Preço Mínimo: {bond.get('minimo', 'N/A')}%
Cotação Anterior: {bond.get('cotacao_anterior', 'N/A')}%
Cotação Actual: {bond.get('cotacao_actual', 'N/A')}%
Variação: {bond.get('variacao', 'N/A')}%""",
                "metadata": {
                    "section": "otnr_bond",
                    "instrument_code": codigo,
                    "bulletin": num,
                    "date": date,
                    "cupao": bond.get('cupao'),
                    "ytm": bond.get('ytm'),
                    "variacao": bond.get('variacao'),
                    "cotacao": bond.get('cotacao_actual'),
                    "maturidade": bond.get('maturidade')
                }
            })

    # --- OT-ME Exchange ---
    otme = bulletin_data.get("otme_exchange", {})
    otme_bonds = otme.get("bonds", [])
    if otme_bonds:
        lines = [
            f"{b.get('codigo','?')}: cupão {b.get('cupao','?')}%, "
            f"YTM {b.get('ytm','?')}%, "
            f"preço {b.get('cotacao_actual','?')}%, "
            f"var {b.get('variacao','?')}%"
            for b in otme_bonds
        ]
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nOT-ME MERCADO DE BOLSA (USD)\n\n" + "\n".join(lines),
            "metadata": {"section": "otme_exchange", "bulletin": num, "date": date}
        })
    else:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nOT-ME Mercado de Bolsa: Não se registaram transacções nesta sessão.",
            "metadata": {"section": "otme_exchange", "bulletin": num, "date": date}
        })

    # --- Corporate Bonds ---
    corp = bulletin_data.get("corporate_bonds", {})
    corp_bonds = corp.get("bonds", [])
    if corp_bonds:
        lines = [
            f"{b.get('codigo','?')}: cupão {b.get('cupao','?')}%, "
            f"YTM {b.get('ytm','?')}%, "
            f"preço {b.get('cotacao_actual','?')}%, "
            f"var {b.get('variacao','?')}%, "
            f"vol {b.get('volume', 0)} unidades"
            for b in corp_bonds
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE OBRIGAÇÕES PRIVADAS (Corporate Bonds)
Total: {corp.get('total_negocios', 0)} negócios | {corp.get('total_volume', 0)} unidades

""" + "\n".join(lines),
            "metadata": {"section": "corporate_bonds", "bulletin": num, "date": date}
        })

    # --- Stocks: summary + one chunk per company ---
    stocks = bulletin_data.get("stocks", {})
    companies = stocks.get("companies", [])

    if companies:
        stock_lines = [
            f"{c.get('codigo','?')} ({c.get('nome','?')}): "
            f"AOA {c.get('cotacao_actual', 0):,.0f} | "
            f"var {c.get('variacao', 0):.2f}% | "
            f"{c.get('negocios', 0)} negócios | "
            f"{c.get('volume', 0):,} acções | "
            f"Cap: AOA {c.get('capitalizacao', 0):,.0f}"
            for c in companies
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE ACÇÕES - RESUMO DA SESSÃO

Total: {stocks.get('total_negocios', 0)} negócios | {stocks.get('total_volume', 0):,} acções
Capitalização Bolsista Total: AOA {stocks.get('total_capitalizacao', 0):,.0f}

""" + "\n".join(stock_lines),
            "metadata": {
                "section": "stocks_summary",
                "bulletin": num,
                "date": date,
                "total_capitalizacao": stocks.get('total_capitalizacao')
            }
        })

        for company in companies:
            codigo = company.get('codigo', 'unknown')
            chunks.append({
                "content": f"""BODIVA Boletim {num} - {date}
ACÇÃO: {codigo} - {company.get('nome', '')}

Código: {codigo}
Nome: {company.get('nome', 'N/A')}
Data de Emissão (admissão): {company.get('emissao', 'N/A')}
Negócios: {company.get('negocios', 0)}
Volume (acções transaccionadas): {company.get('volume', 0):,}
Preço de Abertura: AOA {company.get('abertura', 0):,.4f}
Preço Máximo: AOA {company.get('maximo', 0):,.4f}
Preço Mínimo: AOA {company.get('minimo', 0):,.4f}
Cotação Anterior: AOA {company.get('cotacao_anterior', 0):,.4f}
Cotação Actual: AOA {company.get('cotacao_actual', 0):,.4f}
Variação: {company.get('variacao', 0):.2f}%
Capitalização Bolsista: AOA {company.get('capitalizacao', 0):,.4f}""",
                "metadata": {
                    "section": "stock",
                    "instrument_code": codigo,
                    "bulletin": num,
                    "date": date,
                    "variacao": company.get('variacao'),
                    "preco": company.get('cotacao_actual'),
                    "capitalizacao": company.get('capitalizacao')
                }
            })

    # --- OTC OT-NR ---
    otc = bulletin_data.get("otc_otnr", {})
    otc_bonds = otc.get("bonds", [])
    if otc_bonds:
        lines = [
            f"{b.get('codigo','?')}: cupão {b.get('cupao','?')}%, "
            f"YTM {b.get('ytm','?')}%, "
            f"preço {b.get('cotacao_actual','?')}%, "
            f"var {b.get('variacao','?')}%, "
            f"vol {b.get('volume', 0):,} unidades"
            for b in otc_bonds
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE BALCÃO ORGANIZADO - OT-NR (OTC)
Total: {otc.get('total_negocios', 0)} negócios | {otc.get('total_volume', 0):,} unidades

""" + "\n".join(lines),
            "metadata": {"section": "otc_otnr", "bulletin": num, "date": date}
        })
    else:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nMercado de Balcão OT-NR: Não se registaram transacções nesta sessão.",
            "metadata": {"section": "otc_otnr", "bulletin": num, "date": date}
        })

    # --- Repo Market ---
    repos = bulletin_data.get("repos", {})
    repo_ops = repos.get("operations", [])
    if repo_ops:
        lines = [
            f"Colateral: {r.get('colateral_codigo','?')} | "
            f"Valor Mercado: AOA {r.get('valor_mercado', 0):,.2f} | "
            f"Qtd: {r.get('quantidade', 0):,} | "
            f"Taxa Repo: {r.get('taxa_repo','?')}% | "
            f"Haircut: {r.get('haircut','?')}% | "
            f"Prazo: {r.get('num_dias','?')} dias | "
            f"Vencimento: {r.get('data_vencimento','?')}"
            for r in repo_ops
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
MERCADO DE OPERAÇÕES DE REPORTE (Repo Market)

Total Valor de Compra: AOA {repos.get('total_valor_compra', 0):,.2f}
Total Valor de Recompra: AOA {repos.get('total_valor_recompra', 0):,.2f}
Total Quantidade: {repos.get('total_quantidade', 0):,} unidades
Número de Operações: {len(repo_ops)}

Operações:
""" + "\n".join(lines),
            "metadata": {
                "section": "repos",
                "bulletin": num,
                "date": date,
                "num_repos": len(repo_ops),
                "total_valor": repos.get('total_valor_compra')
            }
        })
    else:
        chunks.append({
            "content": f"BODIVA Boletim {num} - {date}\nMercado de Operações de Reporte: Não se registaram operações nesta sessão.",
            "metadata": {"section": "repos", "bulletin": num, "date": date}
        })

    # --- Yield Curve Kz ---
    yc_kz = bulletin_data.get("yield_curve_kz", [])
    if yc_kz:
        lines = [
            f"{p.get('maturidade','?')}: {p.get('yield','?')}% "
            f"(ontem: {p.get('yield_ontem','?')}%, var: {p.get('variacao_pp','?'):.4f} pp)"
            for p in yc_kz
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
CURVA DE RENDIMENTOS KWANZA (AOA) - Yield Curve

""" + "\n".join(lines),
            "metadata": {
                "section": "yield_curve_kz",
                "bulletin": num,
                "date": date,
                "pontos": yc_kz
            }
        })

    # --- Yield Curve OT-TX ---
    yc_otx_data = bulletin_data.get("yield_curve_otx", {})
    yc_otx = yc_otx_data.get("pontos", [])
    if yc_otx:
        lines = [
            f"{p.get('maturidade','?')}: {p.get('yield','?')}% "
            f"(var: {p.get('variacao_pp','?'):.2f} pp)"
            for p in yc_otx
        ]
        chunks.append({
            "content": f"""BODIVA Boletim {num} - {date}
CURVA DE RENDIMENTOS OT-TX (USD-indexada) - Data Referência: {yc_otx_data.get('data_referencia', 'N/A')}

""" + "\n".join(lines),
            "metadata": {
                "section": "yield_curve_otx",
                "bulletin": num,
                "date": date
            }
        })

    # --- Primary Market ---
    pm = bulletin_data.get("primary_market", {})
    comp_otnr = pm.get("leilao_competitivo", {}).get("otnr", [])
    ncomp_otnr = pm.get("leilao_nao_competitivo", {}).get("otnr", [])
    eventos = bulletin_data.get("eventos_distribuicao", [])

    pm_lines = []
    for item in comp_otnr:
        pm_lines.append(
            f"Leilão Competitivo OT-NR {item.get('maturidade','?')}: "
            f"maturidade {item.get('data_maturidade','?')} | "
            f"cupão {item.get('taxa_cupao','?')}% | "
            f"yield {item.get('taxa_rendimento','?')}% | "
            f"ofertado AOA {item.get('montante_ofertado', 0):,} | "
            f"colocado AOA {item.get('montante_colocado', 0):,} | "
            f"taxa subscrição {item.get('taxa_subscricao','?')}%"
        )
    for item in ncomp_otnr:
        pm_lines.append(
            f"Leilão Não Competitivo OT-NR {item.get('maturidade','?')}: "
            f"maturidade {item.get('data_maturidade','?')} | "
            f"cupão {item.get('taxa_cupao','?')}% | "
            f"ofertado AOA {item.get('montante_ofertado', 0):,} | "
            f"colocado AOA {item.get('montante_colocado', 0):,} | "
            f"taxa subscrição {item.get('taxa_subscricao','?')}%"
        )
    for e in eventos:
        pm_lines.append(
            f"Evento de Distribuição: {e.get('tipo_evento','?')} | "
            f"código {e.get('codigo','?')} | "
            f"emitente {e.get('emitente','?')} | "
            f"moeda {e.get('moeda','?')}"
        )

    if not pm_lines:
        pm_lines = ["Não se registaram emissões no Mercado Primário nesta sessão."]

    total_ofertado = pm.get('total_ofertado', 0)
    total_colocado = pm.get('total_colocado', 0)
    taxa_global = pm.get('taxa_subscricao_global', 0)

    chunks.append({
        "content": f"""BODIVA Boletim {num} - {date}
MERCADO PRIMÁRIO - LEILÕES DE TÍTULOS DO TESOURO

O governo angolano (UGD/Tesouro) tentou captar AOA {total_ofertado:,} no total.
Conseguiu colocar AOA {total_colocado:,} — taxa de subscrição global: {taxa_global:.1f}%.
{"O leilão ficou SUBSUBSCRITO — o governo não captou o total pretendido." if taxa_global < 100 else "Leilão totalmente subscrito."}

""" + "\n".join(pm_lines),
        "metadata": {
            "section": "primary_market",
            "bulletin": num,
            "date": date,
            "total_ofertado": total_ofertado,
            "total_colocado": total_colocado,
            "taxa_subscricao": taxa_global
        }
    })

    return chunks
