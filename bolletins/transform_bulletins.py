"""
Transform bulletin JSONs into the EXACT schema expected by bulletin_json_converter.py.

Handles two known source formats:
  A) "old nested" format (e.g. boletim_2654_20260302.json) - deeply nested
     session_summary, member_performance.membros, repos.operacoes, etc.
  B) "flat-ish" format used for 2722-2725 - close but uses different key
     names than the converter (e.g. otnr_exchange as a bare list instead
     of {"bonds": [...]}).

Output matches converter expectations exactly:
  - session_summary: total_negociado, mercado_titulos_tesouro.{total,
    multilateral.{total,ot_nr,ot_me,bt}, bilateral.{total,ot_nr,ot_me}},
    mercado_obrigacoes_privadas (number), mercado_uniparticipacao (number),
    mercado_acoes (number), mercado_operacoes_reporte (number)
  - member_performance: {"members": [...], "total_montante":, "total_negocios":}
  - otnr_exchange / otme_exchange / corporate_bonds / otc_otnr:
    {"bonds": [...], "total_negocios":, "total_volume":}
  - stocks: {"companies": [...], "total_negocios":, "total_volume":,
    "total_capitalizacao":}
  - repos: {"operations": [...], "total_valor_compra":, "total_valor_recompra":,
    "total_quantidade":}
  - yield_curve_kz: flat list of {"maturidade","yield","yield_ontem","variacao_pp"}
  - yield_curve_otx: {"data_referencia":, "pontos": [{"maturidade","yield","variacao_pp"}]}
  - primary_market: {"leilao_competitivo": {"otnr": [...]}, "leilao_nao_competitivo":
    {"otnr": [...]}, "total_ofertado":, "total_colocado":, "taxa_subscricao_global":}
  - eventos_distribuicao: list of {"tipo_evento","codigo","emitente","moeda"}

Usage:
    python transform_bulletins.py
"""

import json
from pathlib import Path

SOURCE_DIR = Path(r"C:\Users\bruno\utanya-api\bolletins")
OUTPUT_DIR = Path(r"C:\Users\bruno\utanya-api\bolletins\fixed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def num(v, default=0):
    """Coerce to float/int, stripping % signs etc if needed."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip().replace("%", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return default
    return default


def pct(v, default=0):
    """Return percentage as a plain number (e.g. 17.09 not '17.09%')."""
    return num(v, default)


# ---------------------------------------------------------------------------
# Session summary
# ---------------------------------------------------------------------------

def build_session_summary(old):
    ss = old.get("session_summary", {})

    # --- Detect "old nested" format ---
    if "mercado_bolsa_titulos_tesouro" in ss:
        mbt = ss["mercado_bolsa_titulos_tesouro"]
        multi = mbt.get("ambiente_multilateral", {})
        bila = mbt.get("ambiente_bilateral", {})
        multi_ot = multi.get("obrigacoes_tesouro", {})
        bila_ot = bila.get("obrigacoes_tesouro", {})

        return {
            "total_negociado": num(old.get("total_sessao_aoa") or ss.get("total_sessao_aoa")),
            "mercado_titulos_tesouro": {
                "total": num(mbt.get("total_aoa")),
                "multilateral": {
                    "total": num(multi.get("total_aoa")),
                    "ot_nr": num(multi_ot.get("ot_nr")),
                    "ot_me": num(multi_ot.get("ot_me")),
                    "bt": num(multi.get("bilhetes_tesouro")),
                },
                "bilateral": {
                    "total": num(bila.get("total_aoa")),
                    "ot_nr": num(bila_ot.get("ot_nr")),
                    "ot_me": num(bila_ot.get("ot_me")),
                }
            },
            "mercado_obrigacoes_privadas": num(ss.get("mercado_bolsa_obrigacoes_privadas", {}).get("total_aoa")),
            "mercado_uniparticipacao": num(ss.get("mercado_bolsa_unidades_participacao", {}).get("total_aoa")),
            "mercado_acoes": num(ss.get("mercado_bolsa_accoes", {}).get("total_aoa")),
            "mercado_operacoes_reporte": num(ss.get("mercado_operacoes_reporte", {}).get("total_aoa")),
        }

    # --- "flat-ish" format (2722-2725 style) ---
    mt_total = num(ss.get("mercado_titulos_tesouro_total"))
    mt_multi_total = num(ss.get("mercado_titulos_tesouro_multilateral"))
    mt_bila_total = num(ss.get("mercado_titulos_tesouro_bilateral"))

    ot_nr_multi = num(ss.get("ot_nr_multilateral", ss.get("ot_nr")))
    ot_nr_bila = num(ss.get("ot_nr_bilateral"))
    ot_me_multi = num(ss.get("ot_me_multilateral", ss.get("ot_me")))
    ot_me_bila = num(ss.get("ot_me_bilateral"))
    bt_multi = num(ss.get("bt_28_dias")) + num(ss.get("bt_91_dias")) + \
        num(ss.get("bt_182_dias")) + num(ss.get("bt_364_dias"))

    total_negociado = num(ss.get("total_geral"))

    return {
        "total_negociado": total_negociado,
        "mercado_titulos_tesouro": {
            "total": mt_total,
            "multilateral": {
                "total": mt_multi_total,
                "ot_nr": ot_nr_multi,
                "ot_me": ot_me_multi,
                "bt": bt_multi,
            },
            "bilateral": {
                "total": mt_bila_total,
                "ot_nr": ot_nr_bila,
                "ot_me": ot_me_bila,
            }
        },
        "mercado_obrigacoes_privadas": num(ss.get("mercado_obrigacoes_privadas_total")),
        "mercado_uniparticipacao": num(ss.get("mercado_unidades_participacao_total")),
        "mercado_acoes": num(ss.get("mercado_accoes_total")),
        "mercado_operacoes_reporte": num(ss.get("mercado_operacoes_reporte_total")),
    }


# ---------------------------------------------------------------------------
# Member performance
# ---------------------------------------------------------------------------

def build_member_performance(old):
    mp = old.get("member_performance")

    members = []
    total_montante = 0
    total_negocios = 0

    if isinstance(mp, dict) and "membros" in mp:
        for m in mp.get("membros", []):
            members.append({
                "code": m.get("membro"),
                "name": m.get("membro"),
                "montante": num(m.get("montante_aoa")),
                "negocios": int(num(m.get("negocios"))),
                "percentagem": num(m.get("percentagem")),
                "vendas": num(m.get("vendas_interbancarias")),
                "compras": num(m.get("compras_interbancarias")),
                "internos": num(m.get("negocios_internos")),
            })
        tot = mp.get("total", {})
        total_montante = num(tot.get("montante_aoa"))
        total_negocios = int(num(tot.get("negocios")))

    elif isinstance(mp, list):
        for m in mp:
            members.append({
                "code": m.get("membro"),
                "name": m.get("membro"),
                "montante": num(m.get("montante_negociado")),
                "negocios": int(num(m.get("numero_negocios"))),
                "percentagem": num(m.get("percentagem")),
                "vendas": num(m.get("vendas_interbancarias")),
                "compras": num(m.get("compras_interbancarias")),
                "internos": num(m.get("negocios_internos")),
            })
        mt = old.get("members_total", {})
        total_montante = num(mt.get("montante_negociado"))
        total_negocios = int(num(mt.get("numero_negocios")))

    return {
        "members": members,
        "total_montante": total_montante,
        "total_negocios": total_negocios,
    }


# ---------------------------------------------------------------------------
# Generic bond-list builder
# ---------------------------------------------------------------------------

def build_bond_block(old_items, old_total=None):
    bonds = []
    total_negocios = 0
    total_volume = 0

    for it in old_items:
        codigo = it.get("codigo")
        cupao = it.get("cupao")
        if cupao is None:
            cupao = it.get("cupao_yield")
        cupao = pct(cupao)

        ytm = pct(it.get("ytm"))
        cotacao_actual = num(it.get("cotacao_actual"))
        variacao = num(it.get("variacao_pct", it.get("variacao")))
        volume = int(num(it.get("volume_total", it.get("volume"))))
        emissao = it.get("data_emissao", it.get("emissao"))
        maturidade = it.get("data_maturidade", it.get("maturidade"))
        negocios = int(num(it.get("negocios_realizados", it.get("negocios"))))
        abertura = num(it.get("abertura"))
        maximo = num(it.get("maximo"))
        minimo = num(it.get("minimo"))
        cotacao_anterior = num(it.get("cotacao_anterior"))

        bonds.append({
            "codigo": codigo,
            "cupao": cupao,
            "ytm": ytm,
            "cotacao_actual": cotacao_actual,
            "variacao": variacao,
            "volume": volume,
            "emissao": emissao,
            "maturidade": maturidade,
            "negocios": negocios,
            "abertura": abertura,
            "maximo": maximo,
            "minimo": minimo,
            "cotacao_anterior": cotacao_anterior,
        })

        total_negocios += negocios
        total_volume += volume

    if old_total:
        tn = old_total.get("negocios_realizados", old_total.get("negocios"))
        tv = old_total.get("volume_total")
        if tn is not None:
            total_negocios = int(num(tn))
        if tv is not None:
            total_volume = int(num(tv))

    return {
        "bonds": bonds,
        "total_negocios": total_negocios,
        "total_volume": total_volume,
    }


# ---------------------------------------------------------------------------
# Stocks
# ---------------------------------------------------------------------------

def build_stocks(old):
    items = old.get("stocks", [])
    old_total = old.get("stocks_total", {})

    companies = []
    total_negocios = 0
    total_volume = 0
    total_cap = 0

    for it in items:
        negocios = int(num(it.get("negocios_realizados", it.get("negocios"))))
        volume = int(num(it.get("volume_total", it.get("volume"))))
        cap = num(it.get("capitalizacao_bolsista", it.get("capitalizacao")))

        companies.append({
            "codigo": it.get("codigo"),
            "nome": it.get("empresa", it.get("nome", it.get("codigo"))),
            "emissao": it.get("data_emissao", it.get("emissao")),
            "negocios": negocios,
            "volume": volume,
            "abertura": num(it.get("abertura")),
            "maximo": num(it.get("maximo")),
            "minimo": num(it.get("minimo")),
            "cotacao_anterior": num(it.get("cotacao_anterior")),
            "cotacao_actual": num(it.get("cotacao_actual")),
            "variacao": num(it.get("variacao_pct", it.get("variacao"))),
            "capitalizacao": cap,
        })

        total_negocios += negocios
        total_volume += volume
        total_cap += cap

    tn = old_total.get("negocios_realizados", old_total.get("negocios"))
    tv = old_total.get("volume_total")
    tc = old_total.get("capitalizacao_bolsista_total", old_total.get("capitalizacao_total"))
    if tn is not None:
        total_negocios = int(num(tn))
    if tv is not None:
        total_volume = int(num(tv))
    if tc is not None:
        total_cap = num(tc)

    return {
        "companies": companies,
        "total_negocios": total_negocios,
        "total_volume": total_volume,
        "total_capitalizacao": total_cap,
    }


# ---------------------------------------------------------------------------
# Repos
# ---------------------------------------------------------------------------

def build_repos(old):
    operations = []

    if isinstance(old.get("repos"), dict) and "operacoes" in old["repos"]:
        ops_src = old["repos"].get("operacoes", [])
        total_src = old["repos"].get("total", {})
        for op in ops_src:
            operations.append({
                "colateral_codigo": op.get("colateral"),
                "valor_mercado": num(op.get("valor_mercado")),
                "quantidade": int(num(op.get("qtde"))),
                "taxa_repo": num(op.get("taxa_repo")),
                "haircut": num(op.get("haircut")),
                "num_dias": int(num(op.get("n_dias"))),
                "data_vencimento": op.get("data_vencimento"),
            })
        total_valor_compra = num(total_src.get("preco_compra"))
        total_valor_recompra = num(total_src.get("preco_recompra"))
        total_quantidade = int(num(total_src.get("qtde")))

    elif isinstance(old.get("repos"), list):
        ops_src = old.get("repos", [])
        total_src = old.get("repos_total", {})
        for op in ops_src:
            operations.append({
                "colateral_codigo": op.get("codigo"),
                "valor_mercado": num(op.get("valor_mercado")),
                "quantidade": int(num(op.get("qtde"))),
                "taxa_repo": pct(op.get("taxa_repo")),
                "haircut": pct(op.get("haircut_vm")),
                "num_dias": int(num(op.get("numero_dias"))),
                "data_vencimento": op.get("data_vencimento"),
            })
        total_valor_compra = num(total_src.get("preco_compra"))
        total_valor_recompra = num(total_src.get("preco_recompra"))
        total_quantidade = int(num(total_src.get("qtde")))

    else:
        total_valor_compra = 0
        total_valor_recompra = 0
        total_quantidade = 0

    return {
        "operations": operations,
        "total_valor_compra": total_valor_compra,
        "total_valor_recompra": total_valor_recompra,
        "total_quantidade": total_quantidade,
    }


# ---------------------------------------------------------------------------
# Yield curves
# ---------------------------------------------------------------------------

def build_yield_curve_kz(old):
    src = old.get("yield_curve_kz")
    out = []

    if isinstance(src, dict) and "pontos" in src:
        for p in src["pontos"]:
            out.append({
                "maturidade": p.get("maturidade"),
                "yield": num(p.get("taxa_actual")),
                "yield_ontem": num(p.get("taxa_ontem")),
                "variacao_pp": num(p.get("variacao_pp")),
            })
    elif isinstance(src, list):
        for p in src:
            out.append({
                "maturidade": p.get("maturidade"),
                "yield": num(p.get("tx_rend_actual")),
                "yield_ontem": num(p.get("tx_rend_ontem")),
                "variacao_pp": num(p.get("variacao_pp")),
            })

    return out


def build_yield_curve_otx(old):
    src = old.get("yield_curve_otx")
    ref_date = old.get("yield_curve_otx_reference_date")
    pontos = []

    if isinstance(src, dict) and "pontos" in src:
        ref_date = src.get("data_referencia", ref_date)
        for p in src["pontos"]:
            pontos.append({
                "maturidade": p.get("maturidade"),
                "yield": num(p.get("taxa_actual")),
                "variacao_pp": num(p.get("variacao_pp")),
            })
    elif isinstance(src, list):
        for p in src:
            pontos.append({
                "maturidade": p.get("maturidade"),
                "yield": num(p.get("tx_rend_actual")),
                "variacao_pp": num(p.get("variacao_pp")),
            })

    return {
        "data_referencia": ref_date,
        "pontos": pontos,
    }


# ---------------------------------------------------------------------------
# Primary market
# ---------------------------------------------------------------------------

def _pm_otnr_items(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and "status" not in raw:
        return [raw]
    return []


def build_primary_market(old):
    pm = old.get("primary_market", {})

    comp = pm.get("leilao_competitivo", {})
    ncomp = pm.get("leilao_nao_competitivo", {})

    comp_otnr_raw = comp.get("otnr") if isinstance(comp, dict) else None
    ncomp_otnr_raw = ncomp.get("otnr") if isinstance(ncomp, dict) else None

    comp_items = _pm_otnr_items(comp_otnr_raw)
    ncomp_items = _pm_otnr_items(ncomp_otnr_raw)

    def conv_item(it):
        ofertado = num(it.get("montante_ofertado"))
        colocado = num(it.get("montante_colocado"))
        taxa_sub = (colocado / ofertado * 100) if ofertado else 0
        return {
            "maturidade": it.get("maturidade"),
            "data_maturidade": it.get("data_maturidade"),
            "taxa_cupao": pct(it.get("taxa_cupao")),
            "taxa_rendimento": pct(it.get("taxa_rendimento")),
            "montante_ofertado": ofertado,
            "montante_colocado": colocado,
            "taxa_subscricao": round(taxa_sub, 2),
        }

    comp_conv = [conv_item(i) for i in comp_items]
    ncomp_conv = [conv_item(i) for i in ncomp_items]

    total_ofertado = sum(i["montante_ofertado"] for i in comp_conv + ncomp_conv)
    total_colocado = sum(i["montante_colocado"] for i in comp_conv + ncomp_conv)
    taxa_global = (total_colocado / total_ofertado * 100) if total_ofertado else 0

    return {
        "leilao_competitivo": {"otnr": comp_conv},
        "leilao_nao_competitivo": {"otnr": ncomp_conv},
        "total_ofertado": total_ofertado,
        "total_colocado": total_colocado,
        "taxa_subscricao_global": round(taxa_global, 2),
    }


# ---------------------------------------------------------------------------
# Eventos de distribuicao
# ---------------------------------------------------------------------------

def build_eventos(old):
    out = []
    for e in old.get("eventos_distribuicao", []):
        out.append({
            "tipo_evento": e.get("tipo_evento"),
            "codigo": e.get("codigo", e.get("codigo_negociacao")),
            "emitente": e.get("emitente"),
            "moeda": e.get("moeda"),
        })
    return out


# ---------------------------------------------------------------------------
# Main transform
# ---------------------------------------------------------------------------

def transform(old):
    new = {
        "bulletin_number": old.get("bulletin_number"),
        "date": old.get("date"),
        "session_summary": build_session_summary(old),
        "member_performance": build_member_performance(old),
    }

    if isinstance(old.get("otnr_exchange"), list):
        new["otnr_exchange"] = build_bond_block(
            old.get("otnr_exchange", []),
            old.get("otnr_exchange_total") or old.get("otnr_total")
        )
    elif isinstance(old.get("otnr_exchange"), dict):
        new["otnr_exchange"] = old["otnr_exchange"]
    else:
        new["otnr_exchange"] = {"bonds": [], "total_negocios": 0, "total_volume": 0}

    if isinstance(old.get("otme_exchange"), list):
        new["otme_exchange"] = build_bond_block(old.get("otme_exchange", []))
    elif isinstance(old.get("otme_exchange"), dict):
        new["otme_exchange"] = old["otme_exchange"]
    else:
        new["otme_exchange"] = {"bonds": [], "total_negocios": 0, "total_volume": 0}

    if isinstance(old.get("corporate_bonds"), list):
        new["corporate_bonds"] = build_bond_block(
            old.get("corporate_bonds", []),
            old.get("corporate_bonds_total")
        )
    elif isinstance(old.get("corporate_bonds"), dict):
        new["corporate_bonds"] = old["corporate_bonds"]
    else:
        new["corporate_bonds"] = {"bonds": [], "total_negocios": 0, "total_volume": 0}

    if isinstance(old.get("otc_otnr"), list):
        new["otc_otnr"] = build_bond_block(
            old.get("otc_otnr", []),
            old.get("otc_otnr_total")
        )
    elif isinstance(old.get("otc_otnr"), dict):
        new["otc_otnr"] = old["otc_otnr"]
    else:
        new["otc_otnr"] = {"bonds": [], "total_negocios": 0, "total_volume": 0}

    new["stocks"] = build_stocks(old)
    new["repos"] = build_repos(old)
    new["yield_curve_kz"] = build_yield_curve_kz(old)
    new["yield_curve_otx"] = build_yield_curve_otx(old)
    new["primary_market"] = build_primary_market(old)
    new["eventos_distribuicao"] = build_eventos(old)

    return new


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(SOURCE_DIR.glob("boletim_*.json")) + sorted(SOURCE_DIR.glob("Boletim_*.json"))

    ok, errors = 0, []

    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            errors.append((f.name, f"read error: {e}"))
            continue

        out_path = OUTPUT_DIR / f.name

        try:
            new_data = transform(data)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(new_data, fh, ensure_ascii=False, indent=2)
            ok += 1
        except Exception as e:
            errors.append((f.name, f"transform error: {e}"))

    print(f"Transformed OK: {ok}")
    print(f"Errors: {len(errors)}")
    for name, err in errors:
        print(f"  {name}: {err}")
    print(f"\nOutput written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()