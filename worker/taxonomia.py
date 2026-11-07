"""Áreas dos Mapas Culturais -> linguagens do portal, e detecção de UF."""

from __future__ import annotations

import re
import unicodedata

from .modelos import UFS


def sem_acento(texto: str) -> str:
    """Minúsculas sem acentos, preservando o comprimento (offsets continuam válidos)."""
    return "".join(unicodedata.normalize("NFD", c)[0] for c in texto).lower()


# A ordem importa: dança antes de música ("Frevo (dança)"), literatura antes de
# audiovisual ("Audiolivro"), e assim por diante. Cada regra roda sobre o nome da
# área sem acento.
_REGRAS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (linguagem, re.compile(padrao))
    for linguagem, padrao in (
        ("Fotografia", r"fotograf"),
        ("Artes Visuais", r"videoarte"),
        (
            "Teatro & Dança",
            r"danca|ballet|sapateado|breakdance|street jazz|k-pop dance|jazz \(danca\)|"
            r"teatro|circo|circense|comedia|drama|tragedia|musical$|performance$|"
            r"video ?danca|contacao de historias|mimica|marionete",
        ),
        (
            "Literatura",
            r"literar|literatur|livro|leitura|poesia|conto|cordel|romance|cronica|fabula|epopeia|"
            r"ficcao|ensaios|diario|epistola|declamacao|repente|slam|rima e improviso|"
            r"traducao e interpretacao|audiolivro|biblioteca$|jornais|biografia|"
            r"fomento editorial|escrita|mito$|satira|novela",
        ),
        (
            "Cinema & Audiovisual",
            r"cinema|audiovisual|video|filme|documentario|animacao|televisao|rádio|radio|"
            r"podcast|videocast|webserie|obra seriada|midias interativas|jogos eletronicos|"
            r"realidade virtual|conteudo audiovisual|rima e video",
        ),
        (
            "Artes Visuais",
            r"artes visuais|arte de plasticas|pintura|escultura \(artes visuais\)|gravura|"
            r"xilogravura|grafite|arte digital|arte grafica|arte de rua|intervencao urbana|"
            r"videoarte|mosaico|desenho industrial|artes plasticas",
        ),
        (
            "Patrimônio",
            r"patrimonio|museu|museologia|arqueologia|acervo|arquivo|tombad[oa]|memoria|"
            r"paisagens culturais|salvaguarda|cinemateca",
        ),
        (
            "Música",
            r"musica|canto|banda|orquestra|opera|rock|rap$|samba|forro|funk|reggae|sertanejo|"
            r"punk|axe|baiao|carimbo|ciranda|frevo|lambada|maracatu|xaxado|fandango|"
            r"caboclinho|folia de reis|tambor de crioula|moda de viola|jazz|fanfarra|"
            r"sound system|congada|congado",
        ),
    )
)

# Áreas genéricas que não apontam para uma linguagem: vão para Multiáreas sem
# poluir o log de "áreas não mapeadas".
_GENERICAS = re.compile(
    r"^(outra|arte santeira|escultura \(artesanato\)|producao cultural|producao de eventos|"
    r"gestao cultural|gestao criativa|"
    r"politicas e gestao culturais|economia criativa e da cultura|"
    r"economia, producao e areas tecnicas da cultura|areas tecnicas|pesquisa|direito.*|"
    r"acessibilidade cultural|artesanato.*|design.*|cultura.*|culturas.*|transversalidades|"
    r"interseccionalidades|espacos e equipamentos culturais|festas? .*|festejos juninos|"
    r"carnaval|bloco de carnaval|escola de samba|gastronomia|capoeira|comunicacao|"
    r"mid(ia|ias) sociais|humanidades|historia|filosofia|sociologia|psicologia|"
    r"antropologia|linguistica|ciencia politica|biblioteconomia|arquitetura e urbanismo|"
    r"agrofloresta|bioconstrucao|permacultura|lapidacao|bordado|croche|macrame|renda|"
    r"trancagem|lidas campeiras|cavalhadas|festa .*|cultivo e extracao tradicional|"
    r"internet podcasting|moda|webdesign)$"
)

# Título -> linguagem, usado só quando a fonte não traz nenhuma área.
_TITULO: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Fotografia", re.compile(r"fotograf")),
    ("Música", re.compile(r"\bmusica\b|musical")),
    ("Cinema & Audiovisual", re.compile(r"audiovisual|cinema|\bfilmes?\b|documentario")),
    ("Teatro & Dança", re.compile(r"artes cenicas|teatro|danca|\bcirco\b")),
    ("Literatura", re.compile(r"literatur|\blivros?\b|poesia")),
    ("Artes Visuais", re.compile(r"artes visuais|salao de artes|artes plasticas")),
    ("Patrimônio", re.compile(r"patrimonio|museu")),
)


def mapear_area(area: str) -> str | None:
    """Linguagem do portal para uma área. `None` = área sem mapeamento conhecido."""
    nome = sem_acento(area).strip()
    for linguagem, regra in _REGRAS:
        if regra.search(nome):
            return linguagem
    if _GENERICAS.match(nome):
        return "Multiáreas"
    return None


def mapear_linguagens(
    areas: list[str], titulo: str = "", nao_mapeadas: set[str] | None = None
) -> tuple[list[str], str | None]:
    """Retorna (linguagens ordenadas na taxonomia, trecho de origem quando inferido do título)."""
    encontradas: set[str] = set()
    for area in areas:
        linguagem = mapear_area(area)
        if linguagem is None:
            if nao_mapeadas is not None:
                nao_mapeadas.add(area)
            linguagem = "Multiáreas"
        encontradas.add(linguagem)
    trecho: str | None = None
    if not areas:
        norm = sem_acento(titulo)
        for linguagem, regra in _TITULO:
            achado = regra.search(norm)
            if achado:
                encontradas.add(linguagem)
                trecho = trecho or titulo[achado.start() : achado.end()]
    # Multiáreas só fica sozinha: se há linguagem específica, ela é redundante.
    if len(encontradas) > 1:
        encontradas.discard("Multiáreas")
    if not encontradas:
        encontradas.add("Multiáreas")
    from .modelos import LINGUAGENS

    return [x for x in LINGUAGENS if x in encontradas], trecho


_NOMES_UF = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA",
    "ceara": "CE", "distrito federal": "DF", "espirito santo": "ES", "goias": "GO",
    "maranhao": "MA", "mato grosso do sul": "MS", "mato grosso": "MT",
    "minas gerais": "MG", "para": "PA", "paraiba": "PB", "parana": "PR",
    "pernambuco": "PE", "piaui": "PI", "rio de janeiro": "RJ",
    "rio grande do norte": "RN", "rio grande do sul": "RS", "rondonia": "RO",
    "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE",
    "tocantins": "TO",
}  # fmt: skip

_RE_ESTADO = re.compile(
    r"\b(?:estado|governo)\s+d[oae]s?\s+("
    + "|".join(sorted(_NOMES_UF, key=len, reverse=True))
    + r")\b"
)
_RE_CIDADE_UF = re.compile(
    r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wáéíóúâêôãõç]+\s*(?:/|\s-\s)\s*(" + "|".join(UFS) + r")\b"
)


def detectar_uf(texto: str) -> tuple[str, str] | None:
    """UF citada de forma explícita ("Estado do Ceará", "Fortaleza/CE").

    Devolve (uf, trecho literal) ou `None` se não houver menção ou se houver UFs
    diferentes (ambíguo).
    """
    achados: dict[str, str] = {}
    norm = sem_acento(texto)
    for m in _RE_ESTADO.finditer(norm):
        achados.setdefault(_NOMES_UF[m.group(1)], texto[m.start() : m.end()])
    for m in _RE_CIDADE_UF.finditer(texto):
        achados.setdefault(m.group(1), m.group(0))
    if len(achados) == 1:
        uf, trecho = next(iter(achados.items()))
        return uf, trecho
    return None
