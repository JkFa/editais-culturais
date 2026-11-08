"""Elegibilidade para brasileiros e custos cobertos, só a partir do que o texto da fonte escreve.

Regra editorial do portal: nada de adivinhar. `aberto` exige uma frase explícita (qualquer
nacionalidade, América Latina, Brasil...). Qualquer sinal contrário ou conflitante, e a falta de
sinal, resultam em `verificar`. Os padrões cobrem inglês, espanhol e português.
"""

from __future__ import annotations

import re
import unicodedata

from .modelos import CUSTOS_COBERTOS

Elegibilidade = str  # aberto | requer_residencia | requer_parceiro | verificar


def _norm(texto: str) -> str:
    sem = unicodedata.normalize("NFKD", texto)
    sem = "".join(c for c in sem if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem.lower())


def _frases(texto: str) -> list[str]:
    return [f.strip() for f in re.split(r"(?<=[.!?;])\s+|\n+", _norm(texto)) if f.strip()]


# Quem pode se candidatar: precisa estar dito de forma explícita.
_ABERTO = [
    # inglês
    r"(any|all|every) nationalit",
    r"regardless of (their |your )?(nationality|citizenship|country)",
    r"(open|available|eligible)[^.]{0,40}\b(artists|applicants|candidates|professionals)[^.]{0,30}\b(from|of) (all|any|every) (countries|nations|nationalities|over the world)",
    r"(open|available) (to|for) (artists|applicants|candidates|professionals|everyone|anyone)[^.]{0,30}worldwide",
    r"(open|available) (to|for) (international|foreign) (artists|applicants|candidates)",
    r"(artists|applicants|candidates|professionals)[^.]{0,40}(from|of|based in|living in|trained in|working in|residing in)[^.]{0,60}\b(latin america|south america|brazil)\b",
    r"\b(latin america|south america|brazil)\b[^.]{0,40}(artists|applicants|candidates|are (eligible|welcome)|can apply)",
    r"open (call )?(to|for) (all|any)\b[^.]{0,20}(countries|nationalities|artists)",
    r"(artists|applicants|candidates|professionals)[^.]{0,30}\b(from|of) (any|all|every) (country|countries|nation)",
    # espanhol
    r"(cualquier|todas las|toda) nacionalidad",
    r"sin (importar|distincion de) (su )?nacionalidad",
    r"(artistas|personas|candidatos|solicitantes)[^.]{0,40}(de|con residencia en|residentes en)[^.]{0,60}\b(america latina|latinoamerica|brasil|iberoamerica)\b",
    r"\b(brasil)\b[^.]{0,60}(paises (elegibles|participantes|miembros)|podran (participar|presentarse))",
    r"(paises (elegibles|participantes|miembros)|podran (participar|presentarse))[^.]{0,200}\bbrasil\b",
    # português
    r"(qualquer|todas as|toda) nacionalidade",
    r"independentemente da (sua )?nacionalidade",
    r"(artistas|candidatos|proponentes)[^.]{0,40}(de|residentes em|com residencia em)[^.]{0,60}\b(america latina|brasil|ibero-america)\b",
    r"(paises (elegiveis|participantes|membros)|podem (participar|se inscrever))[^.]{0,200}\bbrasil\b",
]
_NEGACAO = re.compile(
    r"\b(not|no|never|except|excluding|excludes?|nao|no se|sin|excepto|exceto|excluindo|excluye)\b"
    r"[^.]{0,40}\b(open|eligible|apply|admit|accept|abierto|elegible|aceptan|aberto|elegivel|aceitam)",
    re.I,
)
_RESIDENCIA = [
    r"(must|should|have to|need to|required to) (live|reside)\b",
    r"\b(residing|residents?|living|domiciled) (in|of)\b",
    r"(must|should|have to|need to|required to) (be|live|reside|work)[^.]{0,30}\b(resident|residing|living|based|registered)\b",
    r"\bresidents? (of|in)\b",
    r"(must|should) (be )?(a )?(citizen|national) of",
    r"(only )?(open|available|restricted|limited) (to|for)[^.]{0,40}\b(residents|citizens|nationals)\b",
    r"(debe|deben|deberan) (residir|vivir|tener residencia|ser (ciudadan|nacional))",
    r"(exclusivamente|solo|unicamente) (para )?(residentes|ciudadanos|nacionales)",
    r"(deve|devem|precisa|precisam) (residir|morar|ter residencia)",
]
_PARCEIRO = [
    r"(must|should|need to|have to|required to|require[sd]?|requires)[^.]{0,60}\b(partner|partners|partnership|consorti(um|a)|co-?applicant)",
    r"(applications?|projects?|proposals?) (must|should|have to|need to) be (submitted|presented|led)[^.]{0,40}(consorti|partnership|group of|organi[sz]ations)",
    r"in partnership with a[^.]{0,30}(organi[sz]ation|institution)[^.]{0,30}(is|are) (required|mandatory)",
    r"at least (two|three|2|3) (partner|organi[sz]ation|countr|institution)",
    r"(deben|deberan|se requiere|es necesario)[^.]{0,60}\b(socios|socias|consorcio|alianza|organizaciones asociadas|entidades asociadas)",
    r"(deve|devem|precisa|precisam|e necessario)[^.]{0,60}\b(parceiro|parceira|parceria|consorcio)",
]


def _frase_que_casa(padroes: list[str], frases: list[str]) -> str | None:
    for f in frases:
        if any(re.search(p, f) for p in padroes):
            return f
    return None


def analisar_elegibilidade(texto: str | None) -> tuple[Elegibilidade, str | None]:
    """(elegibilidade, frase que comprova). `aberto` só com frase explícita e sem sinal contrário."""
    if not texto:
        return "verificar", None
    frases = _frases(texto)
    achados = {
        "aberto": _frase_que_casa(_ABERTO, frases),
        "requer_residencia": _frase_que_casa(_RESIDENCIA, frases),
        "requer_parceiro": _frase_que_casa(_PARCEIRO, frases),
    }
    presentes = [k for k, v in achados.items() if v]
    negacao = any(_NEGACAO.search(f) for f in frases)
    if negacao or len(presentes) != 1:
        return "verificar", None  # nada escrito, ou sinais que se contradizem
    return presentes[0], achados[presentes[0]][:300]  # type: ignore[index]


def classificar_elegibilidade(texto: str | None) -> Elegibilidade:
    return analisar_elegibilidade(texto)[0]


# Custos: a frase precisa ter um verbo de cobertura, o item e nenhuma negação.
_VERBO = re.compile(
    r"\b(cover|covers|covered|covering|pay|pays|paid|provide|provides|provided|providing|"
    r"include|includes|included|including|offer|offers|offered|granted|grants|reimburse|"
    r"reimburses|cubre|cubren|cubrira|cubriran|incluye|incluyen|ofrece|ofrecen|financia|"
    r"financiara|sufraga|cobre|cobrem|inclui|incluem|oferece|oferecem|custeia|custeiam|"
    r"paga|pagam)\b"
)
_SEM = re.compile(
    r"\b(not|no|nor|without|neither|except|nao|sin|ni|tampouco|nem|separately|separadamente|aparte)\b|"
    r"arranged (by|with)|at (their|your|the artist's) own (cost|expense)|"
    r"(is|are|will be) (the )?(responsibility|up) of|corre por (su )?cuenta|por conta (do|da|dos|das)"
)
_ITENS = {
    "viagem": r"\b(travel|airfare|flights?|transport(ation)?|viaje|viajes|pasajes|passagens?|viagem|viagens|traslados?)\b",
    "hospedagem": r"\b(accommodation|lodging|housing|hotel|alojamiento|hospedaje|hospedagem|alojamento)\b",
    "alimentacao": r"\b(meals?|food|per diem|catering|dinners?|lunch(es)?|comidas?|alimentacion|manutencion|refeic(ao|oes)|alimentacao)\b",
    "producao": r"\b(production (budget|costs?|fund|expenses)|costs? of production|presupuesto de produccion|gastos de produccion|custos de producao|orcamento de producao)\b",
    "cache": r"\b(artist(s'|'s)? fees?|performance fees?|honorari(um|a)|cachet|cache|honorarios|fee will be paid)\b",
    "acessibilidade": r"\b(accessibility|access needs|sign language|interpret(er|ing|ation)|accesibilidad|lengua de senas|acessibilidade|libras)\b",
}


def custos_cobertos(texto: str | None) -> list[str] | None:
    """Custos que o texto diz cobrir, na ordem padrão. `None` quando a fonte não diz nada."""
    if not texto:
        return None
    achados: set[str] = set()
    for frase in _frases(texto):
        if not _VERBO.search(frase) or _SEM.search(frase):
            continue
        for item, padrao in _ITENS.items():
            if re.search(padrao, frase):
                achados.add(item)
    ordenados = [c for c in CUSTOS_COBERTOS if c in achados]
    return ordenados or None
