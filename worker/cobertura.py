"""Placar de cobertura (0 a 10): mede, todo dia, o quanto o portal enxerga dos editais do Brasil.

Roda no fim de cada coleta e grava `web/data/cobertura.json` (e a linha do dia em `cobertura_diaria`).
Tudo sai de métricas objetivas (pesos e metas no `METRICAS`); nenhum número é digitado à mão, exceto o
recall auditado, que vem de `worker/referencia/auditoria.json` (escrito por `scripts/auditoria_cobertura.py`).
Sem auditoria o recall fica "não medido" e vale **zero**: a nota nunca é inflada por uma medida que não existe.

Regras de contagem (também publicadas em /sobre/):
- UF coberta: ao menos uma fonte **estadual** própria ativa e com status `ok` (Mapas estadual, página da Secult...).
  Fontes federais (CultBR, MinC) não contam. Fonte `parcial` (cobre só uma área) vale meio ponto.
- Capital / município: idem, com fonte do próprio município (Mapas municipal, página, diário oficial).
- Evidência: valor/proponentes só contam com `proveniencia` do campo; prazo conta se vem de API estruturada ou tem proveniência.
- Higiene: cada edital vencido listado como aberto e cada par duplicado tira 20% da métrica (5 problemas zeram).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .dedup import mesmo_edital
from .modelos import Edital

log = logging.getLogger("cobertura")
RAIZ = Path(__file__).resolve().parent.parent
REFERENCIA = Path(__file__).resolve().parent / "referencia"
SAIDA = RAIZ / "web" / "data" / "cobertura.json"
VERSAO = 1
HISTORICO_MAX = 60
FRESCOR_HORAS = 26
PESO_CULTBR = (
    0.5  # município que usa o CultBR: o portal lê o CultBR todo dia, mas só o que ele publica lá
)
CULTBR_VALIDADE_DIAS = (
    45  # registro de entes mais velho que isso não conta (o script roda toda semana)
)
AMOSTRA_MINIMA_RECALL = 30
FONTES_ESTRUTURADAS = ("mapas:", "cultbr")

# (id, nome, peso, meta, descrição da meta)
METRICAS = [
    ("ufs", "UFs com fonte estadual oficial saudável", 2.0, "27 de 27"),
    ("capitais", "Capitais com fonte municipal ativa", 1.0, "27 de 27"),
    ("municipios", "Municípios com mais de 100 mil habitantes monitorados", 1.5, "80% ou mais"),
    ("recall", "Recall auditado (amostra de referência independente)", 2.5, "90% ou mais"),
    ("valor", "Editais abertos com valor e evidência", 1.0, "70% ou mais"),
    ("prazo_proponentes", "Prazo (95%) e tipo de proponente (80%) com evidência", 0.5, "95% e 80%"),
    ("frescor", "Fontes ativas com coleta ok nas últimas 26 h", 0.5, "95% ou mais"),
    ("higiene", "Editais vencidos listados como abertos e duplicatas", 1.0, "zero"),
]
PESOS = {m[0]: m[2] for m in METRICAS}
assert abs(sum(PESOS.values()) - 10.0) < 1e-9


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _ler(caminho: Path, padrao: Any) -> Any:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


@dataclass
class Referencias:
    ufs: list[dict[str, Any]]
    municipios: list[dict[str, Any]]
    auditoria: dict[str, Any] | None = None
    cultbr: dict[str, Any] | None = None  # entes com edital no CultBR (scripts/entes_cultbr.py)
    diarios: dict[str, Any] | None = (
        None  # municípios com diário oficial no Querido Diário (scripts/municipios_querido_diario.py)
    )

    @classmethod
    def carregar(cls, pasta: Path = REFERENCIA) -> Referencias:
        return cls(
            ufs=_ler(pasta / "ufs_capitais.json", {}).get("ufs", []),
            municipios=_ler(pasta / "municipios_100k.json", {}).get("municipios", []),
            auditoria=_ler(pasta / "auditoria.json", None),
            cultbr=_ler(pasta / "cultbr_entes.json", None),
            diarios=_ler(pasta / "querido_diario_municipios.json", None),
        )


@dataclass
class Territorio:
    fonte: str
    ente: str  # estado | capital | municipio
    uf: str
    municipio: str | None = None
    parcial: bool = False
    saudavel: bool = False
    ativa: bool = True


def territorios_declarados(instancias: list[Any], fontes: list[Any]) -> list[Territorio]:
    """Território de cada fonte: Mapas pela abrangência; as demais pelo bloco `cobertura` do fontes.yaml."""
    saida: list[Territorio] = []
    for i in instancias:
        if getattr(i, "ambito", "brasil") != "brasil" or not i.uf:
            continue
        if i.abrangencia == "estadual":
            saida.append(Territorio(i.fonte, "estado", i.uf, ativa=i.ativa))
        elif i.abrangencia == "municipal" and i.municipio:
            saida.append(Territorio(i.fonte, "municipio", i.uf, i.municipio, ativa=i.ativa))
    for f in fontes:
        c = getattr(f, "cobertura", None)
        if c:
            saida.append(Territorio(f.id, c.ente, c.uf, c.municipio, c.parcial, ativa=f.ativa))
    return saida


def _cobertos_via_diarios(
    diarios: dict[str, Any] | None,
    cobertos: dict[int, float],
    conhecidos: set[int],
    agora: datetime,
) -> int:
    """Meio ponto para o município cujo diário oficial o portal lê todo dia (Querido Diário, texto completo).

    Só os municípios do placar (capitais e mais de 100 mil habitantes). Fonte própria ou CultBR já valendo meio ponto ou mais não mudam.
    Devolve quantos municípios ganharam ponto só por isso.
    """
    if not diarios:
        return 0
    try:
        gerado = datetime.fromisoformat(str(diarios["atualizado_em"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return 0
    if gerado.tzinfo is None:
        gerado = gerado.replace(tzinfo=UTC)
    if agora - gerado > timedelta(days=CULTBR_VALIDADE_DIAS):
        log.warning(
            "querido_diario_municipios.json está velho (%s): ignorado no placar", gerado.date()
        )
        return 0
    novos = 0
    for m in diarios.get("municipios", []):
        ibge = int(m.get("ibge") or 0)
        if ibge in conhecidos and cobertos.get(ibge, 0.0) < PESO_CULTBR:
            novos += 0 if ibge in cobertos else 1
            cobertos[ibge] = PESO_CULTBR
    return novos


def _cobertos_via_cultbr(
    cultbr: dict[str, Any] | None,
    por_nome: dict[tuple[str, str], int],
    cobertos: dict[int, float],
    agora: datetime,
) -> int:
    """Marca (com meio ponto) os municípios que têm edital no CultBR e ainda não têm fonte própria. Devolve quantos."""
    if not cultbr:
        return 0
    try:
        gerado = datetime.fromisoformat(str(cultbr["atualizado_em"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return 0
    if gerado.tzinfo is None:
        gerado = gerado.replace(tzinfo=UTC)
    if agora - gerado > timedelta(days=CULTBR_VALIDADE_DIAS):
        log.warning("cultbr_entes.json está velho (%s): ignorado no placar", gerado.date())
        return 0
    novos = 0
    for e in cultbr.get("entes", []):
        if e.get("tipo") != "municipio" or not e.get("municipio") or not e.get("editais"):
            continue
        ibge = por_nome.get((normalizar(e["municipio"]), e["uf"]))
        if ibge and cobertos.get(ibge, 0.0) < PESO_CULTBR:
            novos += 0 if ibge in cobertos else 1
            cobertos[ibge] = PESO_CULTBR
    return novos


def _peso_fonte(t: Territorio) -> float:
    return 0.5 if t.parcial else 1.0


def _marcar_saude(territorios: list[Territorio], fontes_status: list[dict[str, Any]]) -> None:
    ok = {f["fonte"] for f in fontes_status if f.get("status") == "ok" and f.get("ativa", True)}
    for t in territorios:
        t.saudavel = t.ativa and t.fonte in ok


def _fim(e: dict[str, Any]) -> datetime | None:
    v = e.get("inscricao_fim")
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _tem_evidencia(e: dict[str, Any], *campos: str) -> bool:
    prov = e.get("proveniencia") or {}
    return any(e.get(c) not in (None, [], "") and c in prov for c in campos)


def _prazo_com_evidencia(e: dict[str, Any]) -> bool:
    if not e.get("inscricao_fim"):
        return False
    return (e.get("fonte") or "").startswith(FONTES_ESTRUTURADAS) or "inscricao_fim" in (
        e.get("proveniencia") or {}
    )


def _frac(valor: float, meta: float) -> float:
    return max(0.0, min(1.0, valor / meta)) if meta else 0.0


def _pct(n: int, d: int) -> float:
    return n / d if d else 0.0


def calcular(
    snapshot: dict[str, Any],
    territorios: list[Territorio],
    refs: Referencias,
    agora: datetime,
    anterior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    editais = [e for e in snapshot.get("editais", []) if e.get("ambito", "brasil") == "brasil"]
    fontes_status = snapshot.get("fontes", [])
    _marcar_saude(territorios, fontes_status)
    saudaveis = [t for t in territorios if t.saudavel]

    # 1. UFs
    por_uf: dict[str, float] = {}
    for t in saudaveis:
        if t.ente == "estado":
            por_uf[t.uf] = max(por_uf.get(t.uf, 0.0), _peso_fonte(t))
    n_ufs = len(refs.ufs) or 27
    ufs_pts = sum(por_uf.get(u["uf"], 0.0) for u in refs.ufs)

    # 2. capitais / 3. municípios grandes
    cap_ibge = {u["capital_ibge"]: u for u in refs.ufs}
    por_nome = {(normalizar(m["nome"]), m["uf"]): m["ibge"] for m in refs.municipios}
    por_nome.update({(normalizar(u["capital"]), u["uf"]): u["capital_ibge"] for u in refs.ufs})
    cobertos: dict[int, float] = {}
    for t in saudaveis:
        if t.ente in ("capital", "municipio") and t.municipio:
            ibge = por_nome.get((normalizar(t.municipio), t.uf))
            if ibge:
                cobertos[ibge] = max(cobertos.get(ibge, 0.0), _peso_fonte(t))
    via_cultbr = _cobertos_via_cultbr(refs.cultbr, por_nome, cobertos, agora)
    via_diarios = _cobertos_via_diarios(refs.diarios, cobertos, set(por_nome.values()), agora)
    cap_pts = sum(cobertos.get(i, 0.0) for i in cap_ibge)
    grandes = [m["ibge"] for m in refs.municipios]
    mun_pts = sum(cobertos.get(i, 0.0) for i in grandes)

    # 4. recall
    aud = refs.auditoria or {}
    medido = (aud.get("amostra") or 0) >= AMOSTRA_MINIMA_RECALL
    recall = aud.get("recall") if medido else None

    # 5/6. campos com evidência
    n = len(editais)
    n_valor = sum(1 for e in editais if _tem_evidencia(e, "valor_total", "teto_por_projeto"))
    n_prazo = sum(1 for e in editais if _prazo_com_evidencia(e))
    n_prop = sum(1 for e in editais if _tem_evidencia(e, "proponentes"))

    # 7. frescor: fontes ativas (que não foram desativadas de propósito) com coleta ok recente
    ativas = [f for f in fontes_status if f.get("ativa")]
    limite = agora - timedelta(hours=FRESCOR_HORAS)

    def fresca(f: dict[str, Any]) -> bool:
        u = f.get("ultimo_sucesso")
        try:
            d = datetime.fromisoformat(str(u).replace("Z", "+00:00")) if u else None
        except ValueError:
            d = None
        return bool(
            f.get("status") == "ok" and d and (d if d.tzinfo else d.replace(tzinfo=UTC)) >= limite
        )

    n_frescas = sum(1 for f in ativas if fresca(f))

    # 8. higiene
    vencidos = [e for e in editais if (fim := _fim(e)) and fim < agora]
    objs = []
    for e in editais:
        try:
            objs.append(Edital.model_validate(e))
        except Exception:  # noqa: BLE001 - registro fora do modelo não deve derrubar o placar
            log.warning(
                "edital %s fora do modelo: ignorado na checagem de duplicatas", e.get("chave")
            )
            continue
    duplicatas = [
        (a.titulo, b.titulo)
        for i, a in enumerate(objs)
        for b in objs[i + 1 :]
        if mesmo_edital(a, b)
    ]
    problemas = len(vencidos) + len(duplicatas)

    f_ufs, f_cap = _frac(ufs_pts, n_ufs), _frac(cap_pts, len(refs.ufs) or 27)
    f_mun = _frac(_pct(mun_pts, len(grandes)), 0.8)
    f_rec = _frac(recall or 0.0, 0.9)
    f_val = _frac(_pct(n_valor, n), 0.7)
    f_pp = 0.5 * _frac(_pct(n_prazo, n), 0.95) + 0.5 * _frac(_pct(n_prop, n), 0.8)
    f_fre = _frac(_pct(n_frescas, len(ativas)), 0.95)
    f_hig = max(0.0, 1.0 - 0.2 * problemas)
    fracoes = {"ufs": f_ufs, "capitais": f_cap, "municipios": f_mun, "recall": f_rec, "valor": f_val,
               "prazo_proponentes": f_pp, "frescor": f_fre, "higiene": f_hig}  # fmt: skip
    detalhes = {
        "ufs": {"atual": ufs_pts, "total": n_ufs, "texto": f"{ufs_pts:g} de {n_ufs} UFs"},
        "capitais": {"atual": cap_pts, "total": 27, "texto": f"{cap_pts:g} de 27 capitais"},
        "municipios": {"atual": mun_pts, "total": len(grandes), "pct": round(_pct(mun_pts, len(grandes)), 4),
                       "via_cultbr": via_cultbr, "via_diarios": via_diarios,
                       "texto": f"{mun_pts:g} de {len(grandes)} municípios ({_pct(mun_pts, len(grandes)):.1%})"
                       + (f"; {via_cultbr} só pelo CultBR e {via_diarios} só pelo diário oficial, a meio ponto" if (via_cultbr or via_diarios) else "")},
        "recall": {"medido": medido, "recall": recall, "amostra": aud.get("amostra"), "data": aud.get("data"),
                   "texto": f"{recall:.0%} em {aud.get('amostra')} editais abertos (auditoria de {aud.get('data')}), sobre os " + (aud.get("universo") or "editais da amostra de referência") if medido
                   else (
                       f"não medido: a amostra tem {aud.get('amostra')} editais abertos (mínimo {AMOSTRA_MINIMA_RECALL}); conta zero até lá"
                       if aud.get("amostra") is not None
                       else "não medido (conta zero até a primeira auditoria com 30 ou mais editais)"
                   )},
        "valor": {"atual": n_valor, "total": n, "pct": round(_pct(n_valor, n), 4), "texto": f"{n_valor} de {n} ({_pct(n_valor, n):.0%})"},
        "prazo_proponentes": {"prazo": n_prazo, "proponentes": n_prop, "total": n,
                              "texto": f"prazo {n_prazo}/{n} ({_pct(n_prazo, n):.0%}); proponentes {n_prop}/{n} ({_pct(n_prop, n):.0%})"},
        "frescor": {"atual": n_frescas, "total": len(ativas), "texto": f"{n_frescas} de {len(ativas)} fontes ativas"},
        "higiene": {"vencidos": [e.get("titulo") for e in vencidos], "duplicatas": duplicatas, "problemas": problemas,
                    "texto": f"{len(vencidos)} vencido(s) e {len(duplicatas)} duplicata(s)"},
    }  # fmt: skip
    metricas = [
        {"id": i, "nome": nome, "peso": peso, "meta": meta, "fracao": round(fracoes[i], 4),
         "pontos": round(peso * fracoes[i], 3), **detalhes[i]}
        for i, nome, peso, meta in METRICAS
    ]  # fmt: skip
    nota = round(sum(m["pontos"] for m in metricas), 2)

    # Tabela por UF (para o /sobre/)
    capitais_por_uf = {u["uf"]: u for u in refs.ufs}
    linhas = []
    for u in refs.ufs:
        uf = u["uf"]
        cap = cobertos.get(u["capital_ibge"], 0.0)
        linhas.append({
            "uf": uf, "nome": u["nome"], "capital": u["capital"],
            "abertos": sum(1 for e in editais if e.get("uf") == uf),
            "fonte_estadual": por_uf.get(uf, 0.0),  # 0, 0.5 (parcial) ou 1
            "capital_coberta": cap,
            "fontes": sorted({t.fonte for t in saudaveis if t.uf == uf}),
        })  # fmt: skip
    sem_uf = sum(1 for e in editais if not e.get("uf") or e["uf"] not in capitais_por_uf)

    hoje = agora.astimezone(UTC).date().isoformat()
    historico = [h for h in (anterior or {}).get("historico", []) if h.get("data") != hoje]
    historico.append({"data": hoje, "nota": nota})
    return {
        "versao": VERSAO,
        "gerado_em": agora.isoformat(),
        "data": hoje,
        "nota": nota,
        "metricas": metricas,
        "por_uf": linhas,
        "editais_abertos": n,
        "editais_sem_uf": sem_uf,
        "historico": historico[-HISTORICO_MAX:],
        "dias_seguidos_9": dias_seguidos(historico, 9.0),
    }


def dias_seguidos(historico: list[dict[str, Any]], minimo: float) -> int:
    """Quantos dias consecutivos (terminando no último registro) com nota >= minimo."""
    cont, prev = 0, None
    for h in reversed(sorted(historico, key=lambda x: x["data"])):
        d = date.fromisoformat(h["data"])
        if h["nota"] < minimo or (prev and (prev - d).days != 1):
            break
        cont, prev = cont + 1, d
    return cont


def gerar(
    snapshot: dict[str, Any],
    instancias: list[Any],
    fontes: list[Any],
    agora: datetime,
    saida: Path = SAIDA,
    refs: Referencias | None = None,
) -> dict[str, Any]:
    refs = refs or Referencias.carregar()
    anterior = _ler(saida, None)
    corpo = calcular(snapshot, territorios_declarados(instancias, fontes), refs, agora, anterior)
    novo = json.dumps(corpo, ensure_ascii=False, indent=1) + "\n"
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(novo, encoding="utf-8")
    # Formato do endpoint do shields.io (badge no README): https://shields.io/badges/endpoint-badge
    nota = corpo["nota"]
    cor = (
        "brightgreen"
        if nota >= 9
        else "green"
        if nota >= 7
        else "yellow"
        if nota >= 5
        else "orange"
        if nota >= 3
        else "red"
    )
    badge = {
        "schemaVersion": 1,
        "label": "cobertura",
        "message": f"{nota:.1f}/10".replace(".", ","),
        "color": cor,
    }
    (saida.parent / "cobertura-badge.json").write_text(
        json.dumps(badge, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log.info(
        "cobertura: nota %.2f (%s)",
        corpo["nota"],
        ", ".join(f"{m['id']}={m['pontos']}" for m in corpo["metricas"]),
    )
    return corpo
