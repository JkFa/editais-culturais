"""Extração automática de critérios dos editais (roda no GitHub Actions, depois da coleta).

Fluxo: fila (`extracao_fila`) -> documento do edital (PDF ou página, baixado uma vez, com hash)
-> LLM em partes (esquema estruturado) -> validação -> verificação dos trechos no texto -> `edital_criterios`
com `revisado = false`. Nada vindo do modelo é gravado sem um trecho que exista literalmente no documento.
Ver docs/assistente.md.
"""
