"""Gera worker/referencia/{ufs_capitais,municipios_100k}.json a partir das APIs públicas do IBGE (Censo 2022).

Uso: python -m scripts.gerar_referencia_ibge   (precisa de rede; o resultado é versionado no repositório)
Fontes: https://servicodados.ibge.gov.br/api/v1/localidades/municipios  e  agregado 4714 (população residente, Censo 2022).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

SAIDA = Path(__file__).resolve().parent.parent / "worker" / "referencia"
CAPITAIS = {
    "AC": "Rio Branco", "AL": "Maceió", "AP": "Macapá", "AM": "Manaus", "BA": "Salvador", "CE": "Fortaleza",
    "DF": "Brasília", "ES": "Vitória", "GO": "Goiânia", "MA": "São Luís", "MT": "Cuiabá", "MS": "Campo Grande",
    "MG": "Belo Horizonte", "PA": "Belém", "PB": "João Pessoa", "PR": "Curitiba", "PE": "Recife", "PI": "Teresina",
    "RJ": "Rio de Janeiro", "RN": "Natal", "RS": "Porto Alegre", "RO": "Porto Velho", "RR": "Boa Vista",
    "SC": "Florianópolis", "SP": "São Paulo", "SE": "Aracaju", "TO": "Palmas",
}  # fmt: skip
UFS_NOMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
    "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima",
    "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe", "TO": "Tocantins",
}  # fmt: skip
BASE = "https://servicodados.ibge.gov.br/api"


def main() -> int:
    with httpx.Client(
        timeout=90, headers={"User-Agent": "editais-culturais (github.com/JkFa/editais-culturais)"}
    ) as c:
        municipios = c.get(f"{BASE}/v1/localidades/municipios").raise_for_status().json()
        pop = (
            c.get(
                f"{BASE}/v3/agregados/4714/periodos/2022/variaveis/93",
                params={"localidades": "N6[all]"},
            )
            .raise_for_status()
            .json()
        )
    populacao = {
        int(s["localidade"]["id"]): int(s["serie"]["2022"])
        for s in pop[0]["resultados"][0]["series"]
        if s["serie"]["2022"].isdigit()
    }
    info = {
        m["id"]: (m["nome"], m["microrregiao"]["mesorregiao"]["UF"]["sigla"])
        for m in municipios
        if m.get("microrregiao")
    }
    info.update(
        {
            m["id"]: (m["nome"], m["regiao-imediata"]["regiao-intermediaria"]["UF"]["sigla"])
            for m in municipios
            if not m.get("microrregiao") and m.get("regiao-imediata")
        }
    )
    por_nome = {(n, uf): i for i, (n, uf) in info.items()}
    ufs = []
    for uf, nome_capital in CAPITAIS.items():
        i = por_nome[(nome_capital, uf)]
        ufs.append(
            {
                "uf": uf,
                "nome": UFS_NOMES[uf],
                "capital": nome_capital,
                "capital_ibge": i,
                "capital_populacao": populacao[i],
            }
        )
    grandes = sorted(
        (
            {"ibge": i, "nome": info[i][0], "uf": info[i][1], "populacao": p}
            for i, p in populacao.items()
            if p > 100_000 and i in info
        ),
        key=lambda x: -x["populacao"],
    )
    SAIDA.mkdir(parents=True, exist_ok=True)
    meta = {
        "fonte": "IBGE, Censo 2022 (agregado 4714, variável 93) e API de localidades",
        "gerado_por": "scripts/gerar_referencia_ibge.py",
    }
    (SAIDA / "ufs_capitais.json").write_text(
        json.dumps({**meta, "ufs": ufs}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (SAIDA / "municipios_100k.json").write_text(
        json.dumps({**meta, "corte": 100_000, "municipios": grandes}, ensure_ascii=False, indent=1)
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(ufs)} UFs; {len(grandes)} municípios com mais de 100 mil habitantes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
