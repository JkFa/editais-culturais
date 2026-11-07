"""Normalização determinística: datas, valores em R$, proponentes, marco legal, resumo.

Tudo aqui é regex. Cada achado guarda o trecho literal do texto-fonte
(`Proveniencia.trecho`) para que a UI e a revisão humana possam conferir.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .modelos import (
    LIMITE_RESUMO_ORIGINAL,
    LIMITE_RESUMO_SIMPLES,
    Edital,
    EditalBruto,
    Proveniencia,
)
from .taxonomia import detectar_uf, mapear_linguagens, sem_acento

FUSO_PADRAO = ZoneInfo("America/Sao_Paulo")

# ---------------------------------------------------------------- datas


def _fuso(nome: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(nome) if nome else FUSO_PADRAO
    except Exception:  # fuso desconhecido: cai no padrão do portal
        return FUSO_PADRAO


def _para_local(valor: Any) -> datetime | None:
    """Converte a data da API num datetime com fuso.

    A API de Mapas Culturais devolve `{"date": "2026-11-06 23:59:00.000000",
    "timezone_type": 3, "timezone": "America/Fortaleza"}`. O fuso informado é
    respeitado (nem todo estado está em UTC-3); sem fuso, vale America/Sao_Paulo.
    """
    if valor in (None, ""):
        return None
    if isinstance(valor, dict):
        texto = valor.get("date")
        fuso = _fuso(valor.get("timezone"))
    else:
        texto = str(valor)
        fuso = FUSO_PADRAO
    if not texto:
        return None
    texto = texto.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return dt.astimezone(fuso) if dt.tzinfo else dt.replace(tzinfo=fuso)


def interpretar_inicio(valor: Any) -> datetime | None:
    dt = _para_local(valor)
    return dt.astimezone(UTC) if dt else None


def interpretar_fim(valor: Any) -> tuple[datetime, bool] | None:
    """(fim em UTC, fim_tem_horario).

    Hora 00:00 ou 23:59 é o padrão que o Mapas grava quando a pessoa informa só o dia;
    nesses casos o fim é 23:59:59 do dia e `fim_tem_horario` fica falso. Qualquer
    outra hora é prazo com horário.
    """
    dt = _para_local(valor)
    if dt is None:
        return None
    so_data = (dt.hour, dt.minute) in ((0, 0), (23, 59))
    if so_data:
        dt = datetime.combine(dt.date(), time(23, 59, 59), tzinfo=dt.tzinfo)
    return dt.astimezone(UTC), not so_data


def fim_de_curadoria(valor: str | date | datetime) -> tuple[datetime, bool]:
    """Data da curadoria: 'AAAA-MM-DD' (sem horário) ou ISO com horário e offset."""
    if isinstance(valor, datetime):
        dt = valor if valor.tzinfo else valor.replace(tzinfo=FUSO_PADRAO)
        return dt.astimezone(UTC), True
    if isinstance(valor, date):
        valor = valor.isoformat()
    texto = str(valor).strip()
    if len(texto) == 10:
        d = date.fromisoformat(texto)
        return datetime.combine(d, time(23, 59, 59), tzinfo=FUSO_PADRAO).astimezone(UTC), False
    dt = datetime.fromisoformat(texto)
    dt = dt if dt.tzinfo else dt.replace(tzinfo=FUSO_PADRAO)
    return dt.astimezone(UTC), True


def inicio_de_curadoria(valor: str | date | datetime | None) -> datetime | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        dt = valor if valor.tzinfo else valor.replace(tzinfo=FUSO_PADRAO)
        return dt.astimezone(UTC)
    d = valor if isinstance(valor, date) else date.fromisoformat(str(valor)[:10])
    return datetime.combine(d, time(0, 0), tzinfo=FUSO_PADRAO).astimezone(UTC)


# ---------------------------------------------------------------- valores em R$

_NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?"
_MULT = r"milh(?:ão|ões|ao|oes)|bilh(?:ão|ões|ao|oes)|mil"
# O lookahead rejeita "R$ 14. 440,00" (espaço digitado no meio do número), que de outro
# modo seria lido como R$ 14.
_RE_VALOR = re.compile(
    rf"R\$\s*({_NUM})(?!\d)(?:\s*({_MULT})\b)?(?![.,]\s\d{{3}}(?!\d))", re.IGNORECASE
)
_MULTIPLICADOR = {"mil": 10**3, "milh": 10**6, "bilh": 10**9}

_FRONTEIRA = re.compile(r"(?<!\d)\.(?=\s|$)|[;\n]")
_CUE_TETO_DEPOIS = re.compile(
    r"\bpor\s+(?:cada\s+)?(?:um[a]?\s+)?(?:projeto|proposta|iniciativa|bolsista|candidat\w*|"
    r"proponent\w*|selecionad\w*|contemplad\w*|agente|mestre|mestra|artista|grupo|"
    r"coletivo|premiad\w*|beneficiari\w*|vencedor\w*|obra|evento|espetaculo|participante|"
    r"inscri\w*|pessoa|vaga|cota)|\bcada\b"
)
_CUE_TOTAL_ANTES = re.compile(
    r"(?:total|totalizando|montante|global|orcamento|dotacao|investimento|recursos|"
    r"destinad[oa]s?|aporte|soma)\b[^.;]{0,40}$"
)
_CUE_TOTAL_DEPOIS = re.compile(
    r"^\W{0,3}(?:no total|ao todo|em total|(?:para|a)\s+(?:a\s+)?(?:iniciativa|selecao|"
    r"premiacao|chamamento|edital)|em premios|distribuidos)"
)
# Dinheiro que o proponente paga ou que remunera serviço (parecerista, taxa), não fomento.
_CUE_NAO_FOMENTO = re.compile(
    r"\btaxa\b|cobrad|\bmulta\b|anuidade|mensalidade|ingresso|parecerist|honorario|"
    r"remuneracao|\bcache\b|\bdiarias?\b"
)
# "5 prêmios de R$ 2.000", "01 vaga de R$ 10.000": o valor é por contemplado.
_CUE_TETO_UNITARIO = re.compile(
    r"(?:premios?|bolsas?|vagas?|projetos?|propostas?|cotas?|iniciativas?)\s+(?:no valor\s+)?de\s*$"
)
_CUE_TETO_ANTES = re.compile(
    r"(?:\bate|limite|maximo|teto)(?:\s+(?:o|a))?(?:\s+valor)?(?:\s+de|\s+ate)?\s*:?\s*$"
)


class ValorAchado:
    __slots__ = ("papel", "trecho", "valor")

    def __init__(self, valor: Decimal, papel: str, trecho: str) -> None:
        self.valor = valor
        self.papel = papel
        self.trecho = trecho


def parse_valor(numero: str, multiplicador: str | None) -> Decimal:
    base = Decimal(numero.replace(".", "").replace(",", "."))
    if multiplicador:
        chave = sem_acento(multiplicador)[:5].rstrip("o")
        chave = (
            "milh" if chave.startswith("milh") else "bilh" if chave.startswith("bilh") else "mil"
        )
        base *= _MULTIPLICADOR[chave]
    return base.quantize(Decimal("0.01"))


def _trecho(texto: str, ini: int, fim: int, margem: int = 70) -> str:
    """Janela literal em volta do achado, sem começar ou terminar no meio de uma palavra."""
    a, b = max(0, ini - margem), min(len(texto), fim + margem)
    while a > 0 and a < ini and not texto[a - 1].isspace():
        a += 1
    while b < len(texto) and b > fim and not texto[b].isspace():
        b -= 1
    return re.sub(r"\s+", " ", texto[a:b]).strip()


def extrair_valores(texto: str) -> list[ValorAchado]:
    """Todos os valores em R$ do texto, classificados em `total`, `teto` ou `outro`.

    O papel vem das palavras vizinhas, olhando só a mesma oração: "por projeto" depois
    do valor indica teto individual; "total/montante/destinados" antes (ou "no total"
    depois) indica o valor global; "até" imediatamente antes indica teto.
    """
    norm = sem_acento(texto)
    achados = list(_RE_VALOR.finditer(texto))
    resultado: list[ValorAchado] = []
    for i, m in enumerate(achados):
        valor = parse_valor(m.group(1), m.group(2))
        if valor <= 0:
            continue
        ini_antes = achados[i - 1].end() if i else 0
        antes = _FRONTEIRA.split(norm[max(ini_antes, m.start() - 60) : m.start()])[-1]
        fim_depois = achados[i + 1].start() if i + 1 < len(achados) else len(norm)
        depois = _FRONTEIRA.split(norm[m.end() : min(fim_depois, m.end() + 70)])[0]
        frase = _FRONTEIRA.split(norm[max(0, m.start() - 160) : m.start()])[-1]
        frase += norm[m.start() : m.start() + 160].split(".")[0]
        if _CUE_NAO_FOMENTO.search(frase):
            papel = "outro"
        elif _CUE_TETO_DEPOIS.search(depois) or _CUE_TETO_UNITARIO.search(antes):
            papel = "teto"
        elif _CUE_TOTAL_ANTES.search(antes) or _CUE_TOTAL_DEPOIS.search(depois):
            papel = "total"
        elif _CUE_TETO_ANTES.search(antes):
            papel = "teto"
        else:
            papel = "outro"
        resultado.append(ValorAchado(valor, papel, _trecho(texto, m.start(), m.end())))
    return resultado


def resolver_valores(texto: str) -> dict[str, tuple[Decimal, str]]:
    """Escolhe valor_total e teto_por_projeto. Vários valores do mesmo papel: o maior.

    Se o total resultante for menor que o teto, os dois são incoerentes e o total é
    descartado (preferimos não informar a informar errado).
    """
    escolhidos: dict[str, ValorAchado] = {}
    for achado in extrair_valores(texto):
        if achado.papel == "outro":
            continue
        atual = escolhidos.get(achado.papel)
        if atual is None or achado.valor > atual.valor:
            escolhidos[achado.papel] = achado
    total, teto = escolhidos.get("total"), escolhidos.get("teto")
    if total and teto and total.valor < teto.valor:
        del escolhidos["total"]
    saida: dict[str, tuple[Decimal, str]] = {}
    if "total" in escolhidos:
        saida["valor_total"] = (escolhidos["total"].valor, escolhidos["total"].trecho)
    if "teto" in escolhidos:
        saida["teto_por_projeto"] = (escolhidos["teto"].valor, escolhidos["teto"].trecho)
    return saida


_RE_NUMERO_LIVRE = re.compile(rf"({_NUM})(?:\s*({_MULT})\b)?", re.IGNORECASE)


def trecho_comprova_valor(valor: Decimal, trecho: str) -> bool:
    """Regra de integridade: o trecho de evidência precisa conter o número do valor."""
    for m in _RE_NUMERO_LIVRE.finditer(trecho):
        try:
            if abs(parse_valor(m.group(1), m.group(2)) - valor) < Decimal("0.005"):
                return True
        except ArithmeticError:
            continue
    return False


# ---------------------------------------------------------------- proponentes e marco legal

_NEGACAO = re.compile(
    r"vedad|nao (?:poderao|podem|sera|serao|e permitid)|impedid|proibid|exceto|exceto"
)
_RE_PF = re.compile(r"pessoas? fisicas?|\bp\.?f\.?\b(?=[ ,)/])")
_RE_MEI = re.compile(r"\bmei\b|microempreendedor(?:es|a|as)? individua(?:l|is)")
_RE_PJ = re.compile(r"pessoas? juridicas?")
_RE_SEM_FINS = re.compile(
    r"sem fins (?:lucrativos|economicos)|\bosc\b|organizacoes? da sociedade civil"
)
_RE_COM_FINS = re.compile(r"com fins (?:lucrativos|economicos)")
_RE_COLETIVO = re.compile(r"\bcoletivos?\b|grupos? informais?")


def extrair_proponentes(texto: str) -> dict[str, str]:
    """Códigos de proponente -> trecho. Menções negadas ("vedada a participação") são ignoradas."""
    norm = sem_acento(texto)
    achados: dict[str, str] = {}

    def vale(m: re.Match[str]) -> bool:
        return not _NEGACAO.search(norm[max(0, m.start() - 60) : m.start()])

    def registrar(codigo: str, m: re.Match[str]) -> None:
        achados.setdefault(codigo, _trecho(texto, m.start(), m.end(), 50))

    for m in _RE_PF.finditer(norm):
        if vale(m):
            registrar("PF", m)
    for m in _RE_MEI.finditer(norm):
        if vale(m):
            registrar("MEI", m)
    for m in _RE_COLETIVO.finditer(norm):
        if vale(m):
            registrar("COLETIVO", m)
    for m in _RE_PJ.finditer(norm):
        if not vale(m):
            continue
        vizinho = norm[m.start() : m.end() + 60]
        sem_fins = _RE_SEM_FINS.search(vizinho)
        com_fins = _RE_COM_FINS.search(vizinho)
        if sem_fins and not com_fins:
            registrar("PJ_SEM_FINS", m)
        elif com_fins and not sem_fins:
            registrar("PJ_COM_FINS", m)
        else:
            # "pessoa jurídica" sem qualificação inclui os dois tipos, literalmente.
            registrar("PJ_COM_FINS", m)
            registrar("PJ_SEM_FINS", m)
    for m in _RE_SEM_FINS.finditer(norm):
        if vale(m):
            registrar("PJ_SEM_FINS", m)
    return achados


_MARCOS = (
    ("PNAB", re.compile(r"\bpnab\b|politica nacional aldir blanc")),
    ("LPG", re.compile(r"paulo gustavo|\blpg\b")),
    ("Rouanet", re.compile(r"rouanet")),
    ("Aldir Blanc", re.compile(r"aldir blanc")),
)


def extrair_marco_legal(texto: str) -> tuple[str, str] | None:
    norm = sem_acento(texto)
    for rotulo, regra in _MARCOS:
        m = regra.search(norm)
        if m:
            return rotulo, _trecho(texto, m.start(), m.end(), 40)
    return None


# ---------------------------------------------------------------- resumo


def limpar_texto(texto: str | None) -> str:
    if not texto:
        return ""
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"[*_#`>]+", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


def truncar_palavra(texto: str, limite: int) -> str:
    if len(texto) <= limite:
        return texto
    corte = texto[: limite - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–")
    return corte + "…"


def resumo_sem_llm(texto: str | None) -> str | None:
    """Primeira frase limpa do resumo original, sem cortar palavra (≤ 280)."""
    limpo = limpar_texto(texto)
    if not limpo:
        return None
    frases = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])", limpo)
    resumo = frases[0]
    for seguinte in frases[1:]:
        if len(resumo) >= 60:
            break
        resumo = f"{resumo} {seguinte}"
    return truncar_palavra(resumo, LIMITE_RESUMO_SIMPLES)


# ---------------------------------------------------------------- montagem do Edital


def texto_para_extracao(bruto: EditalBruto, texto_completo: str | None = None) -> str:
    return limpar_texto(texto_completo or bruto.resumo_original)


def padronizar(
    bruto: EditalBruto,
    agora: datetime,
    texto_completo: str | None = None,
    nao_mapeadas: set[str] | None = None,
) -> Edital:
    """EditalBruto -> Edital, só com regras determinísticas.

    `texto_completo` é o texto sem truncar (título + descrição); o regex roda nele e
    `resumo_original` é gravado truncado em 2000 caracteres.
    """
    prov = dict(bruto.proveniencia)
    resumo_original = limpar_texto(bruto.resumo_original) or None
    if resumo_original:
        resumo_original = truncar_palavra(resumo_original, LIMITE_RESUMO_ORIGINAL)

    if bruto.curado:
        linguagens = bruto.linguagens or ["Multiáreas"]
        campos: dict[str, Any] = {
            "proponentes": bruto.proponentes or None,
            "valor_total": bruto.valor_total,
            "teto_por_projeto": bruto.teto_por_projeto,
            "marco_legal": bruto.marco_legal,
            "documentos_exigidos": bruto.documentos_exigidos or None,
            "resumo_simples": bruto.resumo_simples,
        }
        uf, abrangencia, municipio = bruto.uf, bruto.abrangencia, bruto.municipio
    else:
        linguagens, trecho_ling = mapear_linguagens(bruto.areas, bruto.titulo, nao_mapeadas)
        if bruto.areas:
            prov["linguagens"] = Proveniencia(via="api", trecho="; ".join(bruto.areas)[:300])
        elif trecho_ling:
            prov["linguagens"] = Proveniencia(via="regex", trecho=trecho_ling)
        texto = texto_para_extracao(bruto, texto_completo)
        campos = {
            "proponentes": None,
            "valor_total": None,
            "teto_por_projeto": None,
            "marco_legal": None,
            "documentos_exigidos": None,
            "resumo_simples": resumo_sem_llm(bruto.resumo_original),
        }
        for campo, (valor, trecho) in resolver_valores(texto).items():
            campos[campo] = valor
            prov[campo] = Proveniencia(via="regex", trecho=trecho)
        proponentes = extrair_proponentes(texto)
        if proponentes:
            ordem = ["PF", "MEI", "PJ_COM_FINS", "PJ_SEM_FINS", "COLETIVO"]
            campos["proponentes"] = [p for p in ordem if p in proponentes]
            prov["proponentes"] = Proveniencia(
                via="regex", trecho=" | ".join(proponentes[p] for p in ordem if p in proponentes)
            )
        marco = extrair_marco_legal(texto)
        if marco:
            campos["marco_legal"], trecho_marco = marco
            prov["marco_legal"] = Proveniencia(via="regex", trecho=trecho_marco)
        uf, abrangencia, municipio = bruto.uf, bruto.abrangencia, bruto.municipio
        if uf is None:
            achado = detectar_uf(texto)
            if achado:
                uf = achado[0]
                abrangencia = abrangencia or "estadual"
                prov["uf"] = Proveniencia(via="regex", trecho=achado[1])

    edital = Edital(
        fonte=bruto.fonte,
        id_externo=bruto.id_externo,
        url_origem=bruto.url_origem,
        url_inscricao=bruto.url_inscricao,
        url_edital_pdf=bruto.url_edital_pdf,
        titulo=limpar_texto(bruto.titulo),
        orgao_fomento=limpar_texto(bruto.orgao_fomento),
        resumo_original=resumo_original,
        linguagens=linguagens,
        abrangencia=abrangencia,
        uf=uf,
        municipio=municipio,
        num_cotas=bruto.num_cotas,
        exige_contrapartida=bruto.exige_contrapartida,
        cronograma=bruto.cronograma,
        tipo_apoio=bruto.tipo_apoio,
        verificado_em=bruto.verificado_em,
        pendencias=bruto.pendencias,
        inscricao_inicio=bruto.inscricao_inicio,
        inscricao_fim=bruto.inscricao_fim,
        fim_tem_horario=bruto.fim_tem_horario,
        publicado_em=bruto.publicado_em,
        coletado_em=agora,
        atualizado_em=agora,
        proveniencia=prov,
        **campos,
    )
    return edital.model_copy(update={"hash_conteudo": edital.calcular_hash()})


# ---------------------------------------------------------------- radar

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}  # fmt: skip


def janela_em_meses(janela: str | None) -> tuple[int, int] | None:
    """Primeiro e último mês citados em "fim de janeiro a março" -> (1, 3)."""
    if not janela:
        return None
    norm = sem_acento(janela)
    achados = sorted(
        ((m.start(), _MESES[m.group(0)]) for m in re.finditer("|".join(_MESES), norm)),
    )
    if not achados:
        return None
    return achados[0][1], achados[-1][1]


def meses_ate_janela(inicio: int | None, fim: int | None, mes_atual: int) -> int | None:
    """Meses até a janela abrir (0 = estamos dentro dela). Cíclico, considera virada de ano."""
    if inicio is None or fim is None:
        return None
    dentro = (
        inicio <= mes_atual <= fim if inicio <= fim else mes_atual >= inicio or mes_atual <= fim
    )
    return 0 if dentro else (inicio - mes_atual) % 12
