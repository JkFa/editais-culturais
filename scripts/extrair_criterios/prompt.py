"""Prompt de extração estruturada. Texto original do projeto."""

from __future__ import annotations

from .esquema import ETAPAS, SECOES

VERSAO_PROMPT = "v1"

INSTRUCAO = f"""Você lê o texto de um edital cultural brasileiro e extrai, em JSON, as regras que um proponente precisa conhecer para montar o projeto e o orçamento. O texto vem dividido em páginas, marcadas por "=== PÁGINA n ===".

REGRAS (valem para tudo)
1. Copie números EXATAMENTE como estão no texto. Não calcule, não arredonde, não converta.
2. Se uma informação não está no texto, use null (ou lista vazia). NUNCA invente teto, percentual, limite, critério ou prazo. Na dúvida, null.
3. Todo item precisa de "trecho": uma citação CURTA (no máximo 200 caracteres) copiada LITERALMENTE do texto, que sustenta o item. Use "…" para pular partes. Se não consegue citar, não inclua o item.
4. "pagina": o número da página onde está o trecho (use as marcações do texto); null se não souber.
5. Ignore anexos e modelos de formulário em branco; extraia só o que o edital determina.

CAMPOS
- criterios: critérios de seleção/avaliação (nome, descrição curta, peso e nota máxima só se o texto der). Uma linha por critério.
- teto_orcamento: valor MÁXIMO que UM projeto/proposta pode pedir ou orçar (em reais, só o número). NÃO é o valor total do edital.
- teto_por_proponente: máximo que o mesmo proponente pode receber somando propostas, se o texto disser.
- percentuais_max / percentuais_min: limites percentuais do orçamento por etapa ({", ".join(ETAPAS)}), como FRAÇÃO (15% = 0.15), só se o texto fixar o limite. Ex.: "divulgação até 15% do valor" -> etapa divulgacao, 0.15. Gastos administrativos/gestão -> administrativo; acessibilidade -> acessibilidade.
- rubricas_vedadas: despesas que o edital proíbe pagar com o recurso (uma por item, no vocabulário do edital).
- limites_caracteres: limite de caracteres que o formulário ou o plano de trabalho impõe a um campo; associe à seção do assistente ({", ".join(SECOES)}). Só se houver número e você souber a seção.
- campos_exigidos: os campos/itens que o proponente precisa preencher no formulário ou no plano de trabalho, NA ORDEM do edital; "secao" é a seção do assistente que corresponde, ou null.
- contrapartida_exigida: a contrapartida que o edital exige, em uma frase fiel ao texto, ou null se não exige (ou se diz que não há).
- prazo_execucao_meses: prazo máximo de execução do projeto, em meses (inteiro), se o texto disser.
- exige_acessibilidade: true se o edital EXIGE medidas de acessibilidade nos projetos; null se não fala.
- observacoes: no máximo 2 frases sobre algo importante que não coube nos campos (ex.: "os critérios estão em anexo separado"). null se nada.

Responda SOMENTE com o JSON do esquema."""


def montar_prompt(paginas: list[tuple[int, str]], titulo: str | None = None) -> str:
    """`paginas`: [(número, texto)]. HTML entra como uma página só."""
    partes = [INSTRUCAO, ""]
    if titulo:
        partes.append(f"EDITAL: {titulo}")
    partes.append("TEXTO DO EDITAL:")
    for n, t in paginas:
        partes.append(f"=== PÁGINA {n} ===")
        partes.append(t.strip())
    return "\n".join(partes)
