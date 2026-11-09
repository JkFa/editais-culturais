"""Fila de curadoria: candidatos viram Issues do GitHub; aprovados viram editais publicados.

Fluxo (mínimo da v3): o worker abre uma Issue com a etiqueta `candidato` e um bloco YAML com
o que conseguiu extrair. Uma pessoa confere na fonte oficial, completa o YAML e troca a etiqueta
por `aprovado`. No dia seguinte o worker lê as Issues `aprovado` abertas, valida o YAML pelas
mesmas regras da curadoria (evidência obrigatória) e publica; ao passar o prazo, fecha a Issue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
import yaml
from pydantic import BaseModel, Field

from .coletores.curadoria import EditalCurado, ErroCuradoria
from .modelos import EditalBruto

log = logging.getLogger("worker.fila")

API = "https://api.github.com"
ROTULO_CANDIDATO = "candidato"
ROTULO_APROVADO = "aprovado"
ROTULO_AUTO = (
    "auto-publicado"  # a publicação automática publicou este candidato (worker/automatica.py)
)
ROTULO_VETO = "nao-publicar"  # veto humano: a publicação automática nunca publica este candidato
PREFIXO_MARCADOR = "<!-- candidato:"
LIMITE_CANDIDATOS_POR_EXECUCAO = 20
_TRACKING = re.compile(r"^(utm_|gclid|gad_|fbclid|mc_|_ga|ref$)", re.I)


def normalizar_url(url: str) -> str:
    """Chave de comparação entre fontes: sem esquema, www, barra final, fragmento nem rastreio."""
    p = urlparse(url.strip())
    consulta = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k)))
    host = p.netloc.lower().removeprefix("www.")
    caminho = re.sub(r"/+$", "", p.path) or ""
    return urlunparse(("", host, caminho, "", consulta, "")).lstrip("/")


def marcador(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()[:16]


class Candidato(BaseModel):
    """Possível edital que ainda precisa de conferência humana."""

    chave: str  # estável: mesma origem + mesmo link = mesma Issue
    titulo: str
    link_oficial: str
    origem: str  # texto livre que vai no corpo: "descoberto via Radar de Editais (Clube Emma)"
    motivo: str | None = None
    resumo: str | None = None  # texto da página oficial (nunca do agregador), curto
    campos: dict[str, Any] = Field(default_factory=dict)
    diff: list[str] = Field(default_factory=list)

    @property
    def encerrado(self) -> bool:
        """Prazo lido da página já passou: não vale abrir Issue."""
        fim = self.campos.get("inscricao_fim")
        return bool(fim) and str(fim) < datetime.now(UTC).date().isoformat()

    @property
    def id(self) -> str:
        return marcador(self.chave)

    def yaml_proposto(self) -> str:
        base = {
            "id_externo": f"fila-{self.id}",
            "titulo": self.titulo,
            "orgao_fomento": self.campos.get("orgao_fomento"),
            "url_origem": self.link_oficial,
            "resumo_simples": (self.resumo or "")[:280] or None,
            "linguagens": self.campos.get("linguagens") or ["Multiáreas"],
            "abrangencia": self.campos.get("abrangencia"),
            **{k: self.campos[k] for k in ("uf", "municipio") if self.campos.get(k)},
            "inscricao_fim": self.campos.get("inscricao_fim"),
            "fim_tem_horario": self.campos.get("fim_tem_horario", False),
            "verificado_em": None,
            "evidencias": self.campos.get("evidencias") or {},
        }
        return yaml.safe_dump(base, allow_unicode=True, sort_keys=False, width=100)

    def corpo(self) -> str:
        partes = [
            f"{PREFIXO_MARCADOR}{self.id} -->",
            f"**Origem:** {self.origem}",
            f"**Link oficial:** {self.link_oficial}",
        ]
        if self.motivo:
            partes.append(f"**Por que está na fila:** {self.motivo}")
        if self.resumo:
            partes.append(
                "**Trecho da página oficial** (extraído pelo portal, não do agregador):\n\n> "
                + self.resumo.replace("\n", "\n> ")[:700]
            )
        if self.diff:
            partes.append(
                "**O que mudou na página:**\n\n"
                + "\n".join(f"- {linha}" for linha in self.diff[:20])
            )
        if self.campos.get("aviso"):
            partes.append(
                "### O que fazer com este aviso\n"
                "1. Abra o diário e confira o novo prazo.\n"
                "2. Atualize a fonte do edital que já está no portal (curadoria ou a página de origem).\n"
                "3. **Feche esta Issue. Não use a etiqueta `aprovado`:** ela só avisa, não é um edital novo e publicaria uma duplicata."
            )
            return "\n\n".join(partes)
        partes.append(
            "### Como aprovar\n"
            "1. Abra o link oficial e confira o edital.\n"
            "2. Complete o YAML abaixo (`orgao_fomento`, `inscricao_fim`, `verificado_em`, e as `evidencias` "
            "com o trecho literal de cada campo preenchido).\n"
            "3. Troque a etiqueta `candidato` por `aprovado`. O próximo sync publica; se o prazo já passou ou o "
            "YAML for inválido, a Issue recebe um comentário.\n"
            "4. Para descartar, feche a Issue sem a etiqueta `aprovado`."
        )
        partes.append("```yaml\n" + self.yaml_proposto() + "```")
        return "\n\n".join(partes)


class FilaGitHub:
    def __init__(
        self, repo: str, token: str, transporte: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.repo = repo
        self._c = httpx.AsyncClient(
            base_url=API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
            transport=transporte,
        )

    async def fechar(self) -> None:
        await self._c.aclose()

    async def garantir_rotulos(self) -> None:
        for nome, cor, desc in (
            (ROTULO_CANDIDATO, "fbca04", "Possível edital aguardando conferência"),
            (ROTULO_APROVADO, "0e8a16", "Conferido: o próximo sync publica"),
            (ROTULO_AUTO, "1d76db", "Publicado automaticamente (sem conferência humana)"),
            (ROTULO_VETO, "b60205", "Veto: a publicação automática não publica este candidato"),
        ):
            r = await self._c.post(
                f"/repos/{self.repo}/labels", json={"name": nome, "color": cor, "description": desc}
            )
            if r.status_code not in (201, 422):  # 422 = já existe
                log.warning("não consegui criar a etiqueta %s: HTTP %s", nome, r.status_code)

    async def _listar(self, params: dict[str, str]) -> list[dict[str, Any]]:
        saida: list[dict[str, Any]] = []
        for pagina in range(1, 11):
            r = await self._c.get(
                f"/repos/{self.repo}/issues",
                params={**params, "per_page": "100", "page": str(pagina)},
            )
            r.raise_for_status()
            lote = [i for i in r.json() if "pull_request" not in i]
            saida += lote
            if len(r.json()) < 100:
                break
        return saida

    async def marcadores_existentes(self) -> set[str]:
        """Marcadores de todas as Issues `candidato`/`aprovado`, abertas ou fechadas: nada é recriado."""
        achados: set[str] = set()
        for rotulo in (ROTULO_CANDIDATO, ROTULO_APROVADO):
            for issue in await self._listar({"labels": rotulo, "state": "all"}):
                m = re.search(
                    re.escape(PREFIXO_MARCADOR) + r"([0-9a-f]{16})", issue.get("body") or ""
                )
                if m:
                    achados.add(m.group(1))
        return achados

    async def criar(self, c: Candidato) -> int:
        r = await self._c.post(
            f"/repos/{self.repo}/issues",
            json={
                "title": f"Candidato: {c.titulo}"[:240],
                "body": c.corpo(),
                "labels": [ROTULO_CANDIDATO],
            },
        )
        r.raise_for_status()
        return int(r.json()["number"])

    async def candidatos(self) -> list[dict[str, Any]]:
        return await self._listar({"labels": ROTULO_CANDIDATO, "state": "open"})

    async def rotular(self, numero: int, rotulo: str, comentario: str | None = None) -> None:
        r = await self._c.post(
            f"/repos/{self.repo}/issues/{numero}/labels", json={"labels": [rotulo]}
        )
        r.raise_for_status()
        if comentario:
            await self.comentar(numero, comentario)

    async def desrotular(self, numero: int, rotulo: str, comentario: str | None = None) -> None:
        r = await self._c.delete(f"/repos/{self.repo}/issues/{numero}/labels/{rotulo}")
        if r.status_code not in (200, 404):
            r.raise_for_status()
        if comentario:
            await self.comentar(numero, comentario)

    async def aprovados(self) -> list[dict[str, Any]]:
        return await self._listar({"labels": ROTULO_APROVADO, "state": "open"})

    async def comentar(self, numero: int, texto: str) -> None:
        await self._c.post(f"/repos/{self.repo}/issues/{numero}/comments", json={"body": texto})

    async def fechar_issue(
        self, numero: int, texto: str, motivo: str = "completed"
    ) -> httpx.Response:
        """`motivo`: "completed" (feito) ou "not_planned" (descartado: não conta como aprovada)."""
        await self.comentar(numero, texto)
        r = await self._c.patch(
            f"/repos/{self.repo}/issues/{numero}",
            json={"state": "closed", "state_reason": motivo},
        )
        return (
            r  # quem precisa saber se fechou olha o status; o fluxo de aprovadas segue como antes
        )


def yaml_da_issue(corpo: str) -> dict[str, Any] | None:
    m = re.search(r"```ya?ml\n(.*?)```", corpo or "", re.S)
    if not m:
        return None
    try:
        dados = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    return dados if isinstance(dados, dict) else None


def bruto_de_issue(issue: dict[str, Any]) -> EditalBruto:
    """Valida o YAML aprovado pelas regras da curadoria. Levanta `ErroCuradoria` se inválido."""
    dados = yaml_da_issue(issue.get("body") or "")
    if dados is None:
        raise ErroCuradoria("a Issue aprovada não tem bloco YAML válido")
    dados["id_externo"] = f"fila-{issue['number']}"
    try:
        bruto = EditalCurado.model_validate(dados).para_bruto()
    except ValueError as exc:
        raise ErroCuradoria(str(exc)) from exc
    return bruto.model_copy(update={"via": None})


async def publicar_aprovados(
    fila: FilaGitHub | None, agora: datetime
) -> tuple[list[EditalBruto], list[str]]:
    """Editais das Issues `aprovado`. Issue inválida recebe comentário; vencida é fechada."""
    if fila is None:
        return [], []
    brutos: list[EditalBruto] = []
    avisos: list[str] = []
    for issue in await fila.aprovados():
        n = issue["number"]
        try:
            bruto = bruto_de_issue(issue)
        except ErroCuradoria as exc:
            avisos.append(f"Issue #{n} aprovada mas inválida: {exc}")
            if not any("worker:invalida" in (c or "") for c in [issue.get("body") or ""]):
                await fila.comentar(
                    n,
                    f"Não consegui publicar: {str(exc)[:600]}\n\nCorrija o YAML; o próximo sync tenta de novo.",
                )
            continue
        if bruto.inscricao_fim < agora:
            await fila.fechar_issue(n, "Prazo encerrado: o edital saiu do ar. Fechando esta Issue.")
            continue
        brutos.append(bruto)
    return brutos, avisos


def candidatos_json(candidatos: list[Candidato]) -> str:
    return json.dumps([c.model_dump() for c in candidatos], ensure_ascii=False)
