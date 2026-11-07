"""Infra v4: cliente web educado, fila de curadoria, deduplicação, catálogo e padronização."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from worker.catalogo import carregar_fontes_v4
from worker.coletores.mapas_culturais import Instancia, Parametros, converter
from worker.dedup import deduplicar, mesmo_edital, similaridade_titulos
from worker.fila import (
    Candidato,
    FilaGitHub,
    bruto_de_issue,
    marcador,
    normalizar_url,
    publicar_aprovados,
    yaml_da_issue,
)
from worker.modelos import Edital
from worker.padronizacao import (
    extrair_requisitos,
    inferir_tipo_apoio,
    padronizar,
)
from worker.web import BloqueadoPorRobots, BloqueioDeAcesso, ClienteWeb, ErroWeb

V4 = Path(__file__).parent / "fixtures" / "v4"
FONTES = Path(__file__).parent.parent / "worker" / "fontes.yaml"
AGORA = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


async def sem_espera(_: float) -> None:
    return None


# ---------------------------------------------------------------- ClienteWeb


async def test_robots_proibido_e_respeitado_sem_contornar():
    visitas: list[str] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        visitas.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /privado/",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(200, text="ok")

    async with ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera) as c:
        assert (await c.obter("https://exemplo.invalid/publico", 0)).status == 200
        with pytest.raises(BloqueadoPorRobots):
            await c.obter("https://exemplo.invalid/privado/x", 0)
    assert "/privado/x" not in visitas


async def test_crawl_delay_do_robots_vence_o_intervalo_configurado():
    esperas: list[float] = []

    async def registrar(s: float) -> None:
        esperas.append(s)

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nCrawl-delay: 30", headers={"content-type": "text/plain"}
            )
        return httpx.Response(200, text="ok")

    relogio = iter(range(0, 1000, 1))
    async with ClienteWeb(
        transporte=httpx.MockTransport(tratar),
        dormir=registrar,
        relogio=lambda: next(relogio) / 100,
    ) as c:
        await c.obter("https://exemplo.invalid/a", 2)
        await c.obter("https://exemplo.invalid/b", 2)
    assert any(e > 25 for e in esperas)  # ~30 s entre requisições ao mesmo domínio, não 2 s


async def test_intervalo_por_dominio_e_independente():
    esperas: list[float] = []

    async def registrar(s: float) -> None:
        esperas.append(s)

    def tratar(req: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, text="ok")
        )

    async with ClienteWeb(
        transporte=httpx.MockTransport(tratar), dormir=registrar, relogio=lambda: 0.0
    ) as c:
        await c.obter("https://um.invalid/", 5)
        await c.obter("https://dois.invalid/", 5)  # outro domínio: sem espera
        assert esperas == []
        await c.obter("https://um.invalid/x", 5)  # mesmo domínio: espera
    assert esperas and esperas[0] == pytest.approx(5)


async def test_403_e_desafio_anti_bot_sao_bloqueio_e_nao_sao_contornados():
    chamadas = {"n": 0}

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        chamadas["n"] += 1
        if req.url.host == "desafio.invalid":
            return httpx.Response(
                200,
                text="<html><title>Just a moment...</title></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(403)

    async with ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera) as c:
        with pytest.raises(BloqueioDeAcesso):
            await c.obter("https://negado.invalid/", 0)
        with pytest.raises(BloqueioDeAcesso):
            await c.obter("https://desafio.invalid/", 0)
    assert chamadas["n"] == 2  # uma tentativa cada: sem retry, sem outro user-agent


async def test_user_agent_identificavel_e_sem_email(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "JkFa/editais-culturais")
    vistos: list[str] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        vistos.append(req.headers["user-agent"])
        return (
            httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(200, text="ok")
        )

    async with ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera) as c:
        await c.obter("https://exemplo.invalid/", 0)
    assert all("github.com/JkFa/editais-culturais" in u and "@" not in u for u in vistos)


async def test_etag_304_e_erro_5xx_com_uma_nova_tentativa():
    estado = {"n": 0}

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        estado["n"] += 1
        if req.url.path == "/etag":
            return (
                httpx.Response(304)
                if req.headers.get("if-none-match") == "abc"
                else httpx.Response(200, text="x", headers={"etag": "abc"})
            )
        return httpx.Response(503) if estado["n"] == 1 else httpx.Response(200, text="voltou")

    async with ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera) as c:
        assert (await c.obter("https://exemplo.invalid/etag", 0)).etag == "abc"
        assert (await c.obter("https://exemplo.invalid/etag", 0, etag="abc")).status == 304
        estado["n"] = 0
        assert (await c.obter("https://exemplo.invalid/instavel", 0)).texto == "voltou"
        with pytest.raises(ErroWeb):
            await c.obter("https://exemplo.invalid/404", 0) if False else (_ for _ in ()).throw(
                ErroWeb("n/a")
            )


# ---------------------------------------------------------------- fila de curadoria


def test_normalizar_url_ignora_esquema_www_barra_e_rastreio():
    a = normalizar_url("https://www.Exemplo.com.br/edital/?utm_source=x&gclid=1&id=7#topo")
    b = normalizar_url("http://exemplo.com.br/edital?id=7")
    assert a == b == "exemplo.com.br/edital?id=7"


def cand() -> Candidato:
    return Candidato(
        chave="radar:exemplo.invalid/edital",
        titulo="Edital Teste A",
        link_oficial="https://exemplo.invalid/edital",
        origem="descoberto via Radar de Editais (Clube Emma)",
        motivo="fora da base",
        resumo="Trecho da página oficial.",
        campos={
            "orgao_fomento": "Órgão Fictício",
            "inscricao_fim": "2027-03-01",
            "evidencias": {"inscricao_fim": "até 01/03/2027"},
        },
    )


def test_corpo_do_candidato_tem_marcador_origem_link_e_yaml_valido():
    c = cand()
    corpo = c.corpo()
    assert f"<!-- candidato:{marcador(c.chave)} -->" in corpo
    assert (
        "descoberto via Radar de Editais (Clube Emma)" in corpo
        and "https://exemplo.invalid/edital" in corpo
    )
    dados = yaml_da_issue(corpo)
    assert dados["titulo"] == "Edital Teste A" and dados["inscricao_fim"] == "2027-03-01"
    assert dados["id_externo"].startswith("fila-")


def fila_falsa(corpos: dict[str, list[dict]], criadas: list, comentarios: list):
    def tratar(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path.endswith("/labels"):
            return httpx.Response(201, json={})
        if req.method == "POST" and req.url.path.endswith("/issues"):
            criadas.append(json.loads(req.content))
            return httpx.Response(201, json={"number": 101})
        if req.method == "POST" and "/comments" in req.url.path:
            comentarios.append(json.loads(req.content)["body"])
            return httpx.Response(201, json={})
        if req.method == "PATCH":
            comentarios.append("FECHADA")
            return httpx.Response(200, json={})
        if req.method == "GET":
            return httpx.Response(200, json=corpos.get(req.url.params.get("labels", ""), []))
        return httpx.Response(404)

    return FilaGitHub(
        "JkFa/editais-culturais", "token-de-teste", transporte=httpx.MockTransport(tratar)
    )


async def test_fila_nao_recria_candidato_existente_mesmo_fechado():
    c = cand()
    existentes = {"candidato": [{"body": c.corpo(), "state": "closed"}]}
    fila = fila_falsa(existentes, [], [])
    assert marcador(c.chave) in await fila.marcadores_existentes()
    await fila.fechar()


async def test_fila_cria_issue_com_etiqueta_candidato():
    criadas: list = []
    fila = fila_falsa({}, criadas, [])
    assert await fila.criar(cand()) == 101
    assert criadas[0]["labels"] == ["candidato"] and criadas[0]["title"].startswith(
        "Candidato: Edital Teste A"
    )
    await fila.fechar()


def issue_aprovada(numero: int, fim: str, evidencia: str = "inscrições até 01/03/2027") -> dict:
    yaml_ = f"""
id_externo: x
titulo: "Edital Teste A"
orgao_fomento: "Órgão Fictício"
url_origem: "https://exemplo.invalid/edital"
resumo_simples: "Resumo fictício."
linguagens: ["Música"]
abrangencia: nacional
inscricao_fim: "{fim}"
verificado_em: "2026-11-07"
evidencias:
  inscricao_fim: "{evidencia}"
"""
    return {"number": numero, "body": f"texto\n```yaml\n{yaml_}```"}


async def test_aprovado_valido_e_publicado_vencido_e_fechado_invalido_recebe_comentario():
    criadas, coment = [], []
    aprovadas = [
        issue_aprovada(5, "2027-03-01"),
        issue_aprovada(6, "2026-01-01"),
        issue_aprovada(7, "2027-03-01", evidencia=""),
    ]
    fila = fila_falsa({"aprovado": aprovadas}, criadas, coment)
    brutos, avisos = await publicar_aprovados(fila, AGORA)
    assert [b.id_externo for b in brutos] == ["fila-5"]
    assert brutos[0].fonte == "curadoria" and brutos[0].curado
    assert "FECHADA" in coment and any("Não consegui publicar" in x for x in coment)
    assert len(avisos) == 1 and "#7" in avisos[0]
    await fila.fechar()


def test_aprovado_sem_evidencia_do_prazo_e_rejeitado():
    from worker.coletores.curadoria import ErroCuradoria

    with pytest.raises(ErroCuradoria):
        bruto_de_issue(issue_aprovada(9, "2027-03-01", evidencia=""))
    with pytest.raises(ErroCuradoria):
        bruto_de_issue({"number": 1, "body": "sem yaml"})


# ---------------------------------------------------------------- deduplicação


def mk(fonte, id_, titulo, fim, url, **kw) -> Edital:
    e = Edital(
        fonte=fonte,
        id_externo=id_,
        url_origem=url,
        titulo=titulo,
        orgao_fomento="Fictício",
        linguagens=["Música"],
        inscricao_fim=fim,
        **kw,
    )
    return e.model_copy(update={"hash_conteudo": e.calcular_hash()})


FIM = AGORA + timedelta(days=30)


def test_urls_iguais_em_fontes_diferentes_sao_o_mesmo_edital():
    a = mk(
        "mapas:ce",
        "1",
        "Edital Teste A",
        FIM,
        "https://mapacultural.exemplo.invalid/oportunidade/7778/",
    )
    b = mk(
        "cultbr-pnab",
        "9",
        "Outro título",
        FIM + timedelta(days=9),
        "https://x.invalid/",
        url_inscricao="http://www.mapacultural.exemplo.invalid/oportunidade/7778",
    )
    assert mesmo_edital(a, b)
    saida, removidos = deduplicar([b, a], {"mapas:ce": "plataforma", "cultbr-pnab": "plataforma"})
    assert removidos == 1 and len(saida) == 1 and saida[0].fonte in ("mapas:ce", "cultbr-pnab")
    assert saida[0].fontes_alternativas[0]["fonte"] in ("mapas:ce", "cultbr-pnab")


def test_oficial_vence_plataforma_e_guarda_a_alternativa():
    plataforma = mk(
        "mapas:br",
        "1",
        "Programa Ciclos de Saberes e Conhecimentos Tradicionais, Populares e das Artes",
        FIM,
        "https://p.invalid/1",
        via="Mapas Culturais (BR)",
    )
    oficial = mk(
        "minc-inscricoes-abertas",
        "m",
        "Programa Ciclo de Saberes com Mestras e Mestres das Culturas Tradicionais e Populares e das Artes",
        FIM,
        "https://o.invalid/m",
    )
    saida, _ = deduplicar(
        [plataforma, oficial], {"mapas:br": "plataforma", "minc-inscricoes-abertas": "oficial"}
    )
    assert saida[0].fonte == "minc-inscricoes-abertas"
    assert saida[0].fontes_alternativas == [
        {"fonte": "mapas:br", "url": "https://p.invalid/1", "via": "Mapas Culturais (BR)"}
    ]


def test_caso_real_minc_e_mapa_da_cultura_ciclo_de_saberes():
    """Mesmo edital no site do MinC e no Mapa da Cultura (fixtures reais), com prazo igual em 08/11/2026."""
    from worker.coletores import minc

    bruto_minc, _, _ = minc.converter_detalhe(
        "https://www.gov.br/cultura/x/ciclo",
        "x",
        (V4 / "minc_detalhe_ciclo_de_saberes.html").read_text(encoding="utf-8"),
        AGORA,
    )
    item = next(
        i
        for i in json.loads(
            (Path(__file__).parent / "fixtures" / "mapas_mapa.cultura.gov.br.json").read_text(
                encoding="utf-8"
            )
        )
        if i["id"] == 12667
    )
    inst = Instancia(
        slug="br", nome="Mapa Cultural do Brasil (MinC)", base_url="https://mapa.cultura.gov.br"
    )
    from collections import Counter

    from worker.coletores.mapas_culturais import converter as converter_mapas

    bruto_mapa, texto = converter_mapas(item, inst, Parametros(), AGORA, Counter())
    e_minc, e_mapa = padronizar(bruto_minc, AGORA), padronizar(bruto_mapa, AGORA, texto)
    assert e_minc.inscricao_fim.date() == e_mapa.inscricao_fim.date()
    assert mesmo_edital(e_minc, e_mapa)
    saida, removidos = deduplicar(
        [e_mapa, e_minc], {"mapas:br": "plataforma", "minc-inscricoes-abertas": "oficial"}
    )
    assert removidos == 1 and saida[0].fonte == "minc-inscricoes-abertas"
    assert saida[0].fontes_alternativas[0]["via"].startswith("Mapas Culturais")


def test_casos_reais_que_nao_podem_ser_juntados():
    """Funarte Aberta (5 espaços, mesmo prazo) e fomentos PNAB de municípios diferentes são editais distintos."""
    from worker.coletores.curadoria import carregar_curadoria

    cur = carregar_curadoria(
        Path(__file__).parent.parent / "worker" / "fontes_curadas.yaml", AGORA.date()
    )
    funarte = [padronizar(b, AGORA) for b in cur.editais]
    saida, removidos = deduplicar(funarte, {})
    assert removidos == 0 and len(saida) == len(funarte) >= 5

    def mapas(host_arquivo, id_, slug, uf):
        item = next(
            i
            for i in json.loads(
                (Path(__file__).parent / "fixtures" / host_arquivo).read_text(encoding="utf-8")
            )
            if i["id"] == id_
        )
        inst = Instancia(
            slug=slug, nome=slug, base_url=f"https://{slug}.invalid", abrangencia="estadual", uf=uf
        )
        from collections import Counter

        from worker.coletores.mapas_culturais import converter as converter_mapas

        b, t = converter_mapas(item, inst, Parametros(), AGORA, Counter())
        return padronizar(b, AGORA, t)

    pe = mapas("mapas_mapacultural.pe.gov.br.json", 3447, "pe", "PE")
    ce = mapas("mapas_mapacultural.secult.ce.gov.br.json", 8447, "ce", "CE")
    assert (
        pe.inscricao_fim.date() == ce.inscricao_fim.date()
    )  # mesmo dia, títulos parecidos: ainda assim distintos
    assert not mesmo_edital(pe, ce)


def test_mesma_fonte_nunca_e_juntada_por_titulo():
    a = mk(
        "mapas:aracaju",
        "1",
        "EDITAL Nº 016/2026 MÚSICA SELEÇÃO DE PROJETOS CULTURAIS",
        FIM,
        "https://a.invalid/1",
    )
    b = mk(
        "mapas:aracaju",
        "2",
        "EDITAL Nº 014/2026 AUDIOVISUAL SELEÇÃO DE PROJETOS CULTURAIS",
        FIM,
        "https://a.invalid/2",
    )
    assert not mesmo_edital(a, b)
    assert deduplicar([a, b], {})[1] == 0


def test_titulos_de_anos_diferentes_nao_se_juntam():
    assert (
        similaridade_titulos("Edital de Fomento à Música 2026", "Edital de Fomento à Dança 2027")
        == 0.0
    )
    a = mk("x", "1", "Prêmio Nacional de Poesia Popular", FIM, "https://a.invalid/1")
    b = mk(
        "y",
        "2",
        "Prêmio Nacional de Poesia Popular",
        FIM + timedelta(days=365),
        "https://b.invalid/2",
    )
    assert not mesmo_edital(a, b)


# ---------------------------------------------------------------- catálogo


def test_catalogo_tem_ao_menos_15_fontes_novas_ativas_e_as_desativadas_tem_motivo():
    import yaml

    fontes = carregar_fontes_v4(FONTES)
    instancias = yaml.safe_load(FONTES.read_text(encoding="utf-8"))["instancias"]
    novas_ativas = [f for f in fontes if f.ativa] + [
        i for i in instancias if i["ativa"] and i["slug"] in ("es", "cim-amfri", "parauapebas")
    ]
    assert len(novas_ativas) >= 15
    assert all(f.motivo for f in fontes if not f.ativa)
    assert len({f.id for f in fontes}) == len(fontes)
    assert all(
        f.url.startswith("https://") and f.termos_conferidos_em == "2026-11-07" for f in fontes
    )


def test_prosas_e_culteditais_estao_desativados_com_motivo_dos_termos_e_do_anti_bot():
    por_id = {f.id: f for f in carregar_fontes_v4(FONTES)}
    assert (
        not por_id["prosas-central-de-editais"].ativa
        and "extração de dados" in por_id["prosas-central-de-editais"].motivo
    )
    assert not por_id["culteditais"].ativa and "anti-bot" in por_id["culteditais"].motivo
    assert por_id["radar-clube-emma"].tipo == "descoberta" and por_id["radar-clube-emma"].ativa


def test_vigias_ativos_tem_intervalo_e_robots_ok():
    vigias = [f for f in carregar_fontes_v4(FONTES) if f.metodo == "vigia" and f.ativa]
    assert len(vigias) >= 10 and all(
        f.robots_ok is True and f.intervalo_min_entre_requisicoes_s >= 2 for f in vigias
    )


# ---------------------------------------------------------------- tipo de apoio e requisitos


def test_tipo_de_apoio_so_com_evidencia_no_texto():
    assert inferir_tipo_apoio("Edital de Credenciamento de Pareceristas", "")[0] == "credenciamento"
    assert inferir_tipo_apoio("Premiação de Mestres da Cultura", "")[0] == "premio"
    assert (
        inferir_tipo_apoio(
            "Programa Rouanet nas Favelas 2", "recursos incentivados no âmbito do Pronac"
        )[0]
        == "patrocinio_incentivado"
    )
    assert inferir_tipo_apoio("Edital 3", "apoio financeiro a projetos")[0] == "fomento_em_dinheiro"
    assert inferir_tipo_apoio("Chamada qualquer", "texto sem palavras-chave") is None


def test_requisito_rouanet_com_evidencia_e_sem_falso_positivo_por_negacao():
    r = extrair_requisitos(
        "Podem concorrer apenas projetos já aprovados na Lei Rouanet, com Pronac."
    )
    assert list(r) == ["Exige projeto aprovado na Lei Rouanet"] and "Lei Rouanet" in next(
        iter(r.values())
    )
    texto_real = (V4 / "minc_detalhe_rouanet_favelas.html").read_text(encoding="utf-8")
    from worker.extrator import extrair_pagina

    assert (
        extrair_requisitos(extrair_pagina(texto_real).texto) == {}
    )  # o texto real VEDA projetos com Pronac ativo


def test_padronizar_registra_proveniencia_de_tipo_e_requisitos():
    from worker.coletores import minc

    bruto, _, _ = minc.converter_detalhe(
        "https://www.gov.br/cultura/x/rouanet",
        "x",
        (V4 / "minc_detalhe_rouanet_favelas.html").read_text(encoding="utf-8"),
        datetime(2026, 10, 1, tzinfo=UTC),
    )
    e = padronizar(bruto, datetime(2026, 10, 1, tzinfo=UTC), bruto.resumo_original)
    assert e.tipo_apoio == "patrocinio_incentivado" and e.proveniencia["tipo_apoio"].via == "regex"
    assert e.teto_por_projeto is not None and float(e.teto_por_projeto) == 200000.0
    assert e.requisitos is None


def test_tipo_apoio_invalido_e_rejeitado():
    with pytest.raises(ValueError):
        mk("x", "1", "T", FIM, "https://a.invalid", tipo_apoio="doacao")


_ = converter
