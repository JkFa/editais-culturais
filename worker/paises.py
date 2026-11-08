"""Nomes de países (inglês, espanhol e português) para código ISO-3166 alfa-2."""

from __future__ import annotations

from .taxonomia import sem_acento

_NOMES = {
    "AR": "argentina", "AT": "austria", "AU": "australia", "BE": "belgium|belgica|belgica",
    "BG": "bulgaria", "BO": "bolivia", "BR": "brazil|brasil", "CA": "canada",
    "CH": "switzerland|suiza|suica", "CL": "chile", "CN": "china", "CO": "colombia",
    "CR": "costa rica", "CU": "cuba", "CY": "cyprus|chipre", "CZ": "czech republic|czechia|chequia",
    "DE": "germany|alemania|alemanha", "DK": "denmark|dinamarca", "DO": "dominican republic|republica dominicana",
    "EC": "ecuador|equador", "EE": "estonia", "EG": "egypt|egipto|egito",
    "ES": "spain|espana|espanha", "FI": "finland|finlandia", "FR": "france|francia|franca",
    "GB": "united kingdom|uk|reino unido|great britain", "GR": "greece|grecia", "GT": "guatemala",
    "HK": "hong kong", "HR": "croatia|croacia", "HU": "hungary|hungria", "ID": "indonesia",
    "IE": "ireland|irlanda", "IL": "israel", "IN": "india", "IS": "iceland|islandia",
    "IT": "italy|italia", "JP": "japan|japon|japao", "KE": "kenya|quenia", "KR": "south korea|corea del sur|coreia do sul",
    "LT": "lithuania|lituania", "LU": "luxembourg|luxemburgo", "LV": "latvia|letonia", "MA": "morocco|marruecos|marrocos",
    "MG": "madagascar", "MU": "mauritius|mauricio", "MX": "mexico", "MZ": "mozambique|mozambique|mocambique",
    "NL": "netherlands|the netherlands|paises bajos|holanda|paises baixos", "NO": "norway|noruega", "NZ": "new zealand|nueva zelanda|nova zelandia",
    "PA": "panama", "PE": "peru", "PL": "poland|polonia", "PT": "portugal", "PY": "paraguay|paraguai",
    "RO": "romania|rumania", "RS": "serbia", "SC": "seychelles", "SE": "sweden|suecia",
    "SI": "slovenia|eslovenia|eslovenia", "SK": "slovakia|eslovaquia", "SV": "el salvador",
    "TR": "turkey|turkiye|turquia", "UA": "ukraine|ucrania", "UG": "uganda",
    "US": "united states of america|united states|usa|us|estados unidos|eua", "UY": "uruguay|uruguai",
    "VE": "venezuela", "ZA": "south africa|sudafrica|africa do sul", "KM": "comoros|comores",
}  # fmt: skip

POR_NOME: dict[str, str] = {
    sem_acento(nome): codigo for codigo, nomes in _NOMES.items() for nome in nomes.split("|")
}


def codigo_do_pais(nome: str) -> str | None:
    return POR_NOME.get(sem_acento(nome.strip().rstrip(".")))
