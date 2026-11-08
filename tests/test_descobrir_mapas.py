"""Descobridor de instâncias do Mapas Culturais: funil de DNS/robots/API, sem rede e sem tocar em site nenhum."""

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import yaml

from scripts import descobrir_mapas as dm
from worker.scraper_worker import carregar_fontes
from worker.web import ClienteWeb

RAIZ = Path(__file__).resolve().parent.parent
HOJE = date(2026, 11, 8)
UFS = [
    {"uf": "AP", "nome": "Amapá", "capital": "Macapá", "capital_ibge": 1600303},
    {"uf": "DF", "nome": "Distrito Federal", "capital": "Brasília", "capital_ibge": 5300108},
    {"uf": "SP", "nome": "São Paulo", "capital": "São Paulo", "capital_ibge": 3550308},
]
MUNICIPIOS = [
    {"ibge": 3550308, "nome": "São Paulo", "uf": "SP", "populacao": 11_000_000},
    {"ibge": 4314407, "nome": "Pelotas", "uf": "RS", "populacao": 325_000},
    {"ibge": 3547809, "nome": "Santo André", "uf": "SP", "populacao": 748_000},
]
REFS = {"ufs": UFS, "municipios": MUNICIPIOS}
FONTES_MINI = """
instancias:
  - slug: ce
    nome: Mapa Cultural do Ceará
    base_url: https://mapacultural.secult.ce.gov.br
    abrangencia: estadual
    uf: CE
    ativa: true

# ---------------------------------------------------------------------------------------------
# v4: novas fontes.
# ---------------------------------------------------------------------------------------------
fontes: []
"""


def rodar(c):
    return asyncio.run(c)


def dns_falso(existentes: set[str], wildcard: tuple[str, ...] = ()):
    async def resolver(host: str) -> bool:
        if host in existentes:
            return True
        return any(host.endswith("." + z) for z in wildcard)

    return resolver


def web(roteador: dict[str, httpx.Response | Exception], robots: dict[str, str] | None = None):
    pedidos: list[str] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.endswith("/robots.txt"):
            txt = (robots or {}).get(req.url.host)
            return httpx.Response(
                200 if txt is not None else 404,
                text=txt or "",
                headers={"content-type": "text/plain"},
            )
        pedidos.append(url)
        r = roteador.get(req.url.host, httpx.Response(404))
        if isinstance(r, Exception):
            raise r
        return r

    async def sem_espera(_):
        return None

    return ClienteWeb(transporte=httpx.MockTransport(tratar), dormir=sem_espera), pedidos


def mapas(itens=None):
    return httpx.Response(
        200, json=itens if itens is not None else [{"id": 1, "name": "Edital de Teste"}]
    )


# ── candidatos ───────────────────────────────────────────────────────────────────────────────


def test_candidatos_cobrem_uf_capital_municipio_e_prioridade():
    cs = dm.gerar_candidatos(UFS, MUNICIPIOS, {"mapas.xyz.rs.gov.br", "mapacultural.vale.org.br"})
    hosts = [c.host for c in cs]
    assert len(hosts) == len(set(hosts)), "sem host repetido"
    assert {"mapacultural.ap.gov.br", "mapa.cultura.sp.gov.br"} <= set(hosts)
    assert {
        "mapacultural.pelotas.rs.gov.br",
        "mapacultural.santoandre.sp.gov.br",
        "mapa.cultura.santoandre.sp.gov.br",
    } <= set(hosts)
    por_host = {c.host: c for c in cs}
    assert por_host["mapacultural.saopaulo.sp.gov.br"].ente == "capital", (
        "capital identificada pelo código IBGE"
    )
    assert por_host["mapacultural.pelotas.rs.gov.br"].ente == "municipio"
    assert (
        por_host["mapas.xyz.rs.gov.br"].origem == "link-visto"
        and por_host["mapas.xyz.rs.gov.br"].uf == "RS"
    )
    assert hosts[0] in {"mapas.xyz.rs.gov.br", "mapacultural.vale.org.br"}, (
        "hosts vistos em links vão primeiro"
    )
    assert [c.prioridade for c in cs] == sorted(c.prioridade for c in cs)


def test_hosts_de_mapas_nos_links_do_snapshot():
    snap = {
        "editais": [
            {
                "url_origem": "https://mapacultural.exemplo.mg.gov.br/oportunidade/9/",
                "url_inscricao": None,
                "url_edital_pdf": "https://www.exemplo.gov.br/a.pdf",
            },
            {
                "url_origem": "https://mapa.cultura.gov.br/x",
                "fontes_alternativas": [
                    {"url": "https://mapas.outra.sp.gov.br/y"},
                    "https://prosas.com.br/z",
                ],
            },
        ],
        "radar": [
            {"url": "https://www.mapacultural.teste.rj.gov.br/e/1"},
            {"url": "https://nada.com"},
        ],
    }
    assert dm.hosts_de_mapas_nos_links(snap) == {
        "mapacultural.exemplo.mg.gov.br",
        "mapa.cultura.gov.br",
        "mapas.outra.sp.gov.br",
        "mapacultural.teste.rj.gov.br",
    }


def test_conhecidos_e_cache_filtram_o_que_nao_precisa_ser_sondado():
    cs = [
        dm.Candidato(h, "estado", "CE")
        for h in ("mapacultural.secult.ce.gov.br", "a.test", "b.test", "c.test", "d.test", "e.test")
    ]
    sondados = {
        "a.test": {"data": "2026-10-01", "resultado": "nao_mapas"},  # 38 dias: ainda vale
        "b.test": {"data": "2026-07-01", "resultado": "nao_mapas"},  # 130 dias: retesta
        "c.test": {"data": "2026-11-01", "resultado": "inalcancavel"},  # 7 dias de 14: ainda vale
        "d.test": {"data": "2026-10-01", "resultado": "proposto"},  # já virou bloco: nunca de novo
    }
    saida = [c.host for c in dm.a_sondar(cs, dm.hosts_conhecidos(FONTES_MINI), sondados, HOJE)]
    assert saida == ["b.test", "e.test"]


# ── DNS ──────────────────────────────────────────────────────────────────────────────────────


def test_dns_descarta_wildcard_e_nao_resolvidos():
    cs = [dm.Candidato(h, "estado", "DF") for h in ("mapacultural.df.gov.br", "mapa.cultura.df.gov.br")] + [
        dm.Candidato("mapacultural.ap.gov.br", "estado", "AP"),
        dm.Candidato("mapa.cultura.ap.gov.br", "estado", "AP"),
    ]  # fmt: skip
    resolve = dns_falso({"mapacultural.ap.gov.br"}, wildcard=("df.gov.br",))
    reais, nao, wild = rodar(dm.filtrar_por_dns(cs, resolve))
    assert [c.host for c in reais] == ["mapacultural.ap.gov.br"]
    assert [c.host for c in nao] == ["mapa.cultura.ap.gov.br"]
    assert "df.gov.br" in wild and not any("ap.gov.br" in z for z in wild), (
        "resolver 'qualquer coisa.df.gov.br' prova que o domínio responde a tudo"
    )


# ── sondagem ─────────────────────────────────────────────────────────────────────────────────


def sondar(roteador, robots=None, host="mapacultural.ap.gov.br"):
    cliente, pedidos = web({host: roteador} if not isinstance(roteador, dict) else roteador, robots)

    async def corrida():
        async with cliente:
            return await dm.sondar(cliente, dm.Candidato(host, "estado", "AP"))

    return rodar(corrida()), pedidos


def test_sonda_mapas_de_verdade_faz_uma_unica_requisicao_a_api():
    s, pedidos = sondar(mapas())
    assert (s.resultado, s.confianca, s.http, s.robots) == ("mapas", "alta", 200, "permitido")
    assert s.amostra == {"id": 1, "name": "Edital de Teste"}
    assert (
        pedidos
        == ["https://mapacultural.ap.gov.br/api/opportunity/find?%40limit=1&%40select=id%2Cname"]
        or len(pedidos) == 1
    )
    assert "api/opportunity/find" in pedidos[0] and len(pedidos) == 1


def test_lista_vazia_e_mapas_com_confianca_baixa():
    s, _ = sondar(mapas([]))
    assert (s.resultado, s.confianca) == ("mapas", "baixa") and "confirmar" in s.detalhe


def test_nao_mapas_quando_e_html_json_estranho_ou_404():
    for resp in (
        httpx.Response(200, text="<html>oi</html>", headers={"content-type": "text/html"}),
        httpx.Response(200, json={"erro": 1}),
        mapas([{"foo": 1}]),
        httpx.Response(404),
    ):
        s, _ = sondar(resp)
        assert s.resultado == "nao_mapas", resp


def test_bloqueio_403_e_robots_nao_sao_contornados():
    s, pedidos = sondar(httpx.Response(403))
    assert s.resultado == "bloqueado" and len(pedidos) == 1, "403: uma tentativa e para"
    s, pedidos = sondar(
        mapas(), robots={"mapacultural.ap.gov.br": "User-agent: *\nDisallow: /api/"}
    )
    assert s.resultado == "bloqueado" and s.robots.startswith("NÃO permite") and pedidos == [], (
        "robots proíbe: nem tenta a API"
    )


def test_host_que_nao_responde_vira_inalcancavel():
    s, _ = sondar(httpx.ConnectTimeout("lento"))
    assert s.resultado == "inalcancavel"


# ── funil completo ───────────────────────────────────────────────────────────────────────────


def funil(roteador, existentes, **kw):
    cliente, pedidos = web(roteador)
    sondados: dict = kw.pop("sondados", {})

    async def corrida():
        async with cliente:
            return await dm.descobrir(
                FONTES_MINI,
                sondados,
                REFS,
                {"editais": [], "radar": []},
                cliente,
                HOJE,
                resolvedor=dns_falso(existentes, kw.pop("wildcard", ())),
                **kw,
            )

    return rodar(corrida()), sondados, pedidos


def test_funil_acha_mapas_ignora_nao_mapas_e_so_toca_em_quem_resolve():
    roteador = {
        "mapacultural.ap.gov.br": mapas(),
        "mapacultural.pelotas.rs.gov.br": mapas([]),
        "mapa.cultura.sp.gov.br": httpx.Response(
            200, text="<html/>", headers={"content-type": "text/html"}
        ),
    }
    res, sondados, pedidos = funil(
        roteador, set(roteador) | {"mapacultural.df.gov.br"}, wildcard=("df.gov.br",)
    )
    assert sorted(s.candidato.host for s in res.achados) == [
        "mapacultural.ap.gov.br",
        "mapacultural.pelotas.rs.gov.br",
    ]
    assert sorted(u.split("/")[2] for u in pedidos) == sorted(roteador), (
        "nenhuma requisição a host que não resolve (nem ao wildcard do DF)"
    )
    assert (
        sondados["mapa.cultura.sp.gov.br"]["resultado"] == "nao_mapas"
        and sondados["mapacultural.ap.gov.br"]["resultado"] == "proposto"
    )
    assert not any(h for h in sondados if "df.gov.br" in h), "wildcard não vai para o cache"
    assert "mapacultural.secult.ce.gov.br" not in sondados, (
        "o que já está no fontes.yaml não é sondado"
    )


def test_funil_respeita_o_maximo_de_sondas_e_o_cache_na_segunda_rodada():
    roteador = {
        h: mapas()
        for h in (
            "mapacultural.ap.gov.br",
            "mapacultural.pelotas.rs.gov.br",
            "mapa.cultura.sp.gov.br",
        )
    }
    res, sondados, pedidos = funil(roteador, set(roteador), max_sondas=2)
    assert len(res.sondagens) == 2 and len(pedidos) == 2
    res2, _, pedidos2 = funil(roteador, set(roteador), max_sondas=2, sondados=sondados)
    assert [s.candidato.host for s in res2.sondagens] == ["mapa.cultura.sp.gov.br"] or len(
        res2.sondagens
    ) == 1, "só o que sobrou"
    assert len(pedidos2) == 1


def test_so_dns_nao_faz_nenhuma_requisicao():
    res, _, pedidos = funil(
        {"mapacultural.ap.gov.br": mapas()}, {"mapacultural.ap.gov.br"}, so_dns=True
    )
    assert res.resolvem == 1 and pedidos == [] and res.sondagens == []


# ── fontes.yaml e PR ─────────────────────────────────────────────────────────────────────────


def test_bloco_gerado_e_yaml_valido_inativo_e_carrega_como_instancia(tmp_path):
    a = dm.Sondagem(
        dm.Candidato("mapacultural.ap.gov.br", "estado", "AP"),
        "mapas",
        http=200,
        robots="permitido",
        confianca="alta",
        amostra={"name": "X"},
    )
    b = dm.Sondagem(
        dm.Candidato("mapacultural.pelotas.rs.gov.br", "municipio", "RS", "Pelotas"),
        "mapas",
        http=200,
        robots="permitido",
        confianca="baixa",
    )
    usados = {"ce"}
    sa, ba = dm.bloco_yaml(a, HOJE, usados)
    sb, bb = dm.bloco_yaml(b, HOJE, usados)
    assert (sa, sb) == ("ap", "pelotas-rs")
    novo = dm.inserir_instancias(FONTES_MINI, [ba, bb])
    dados = yaml.safe_load(novo)
    ap, pel = dados["instancias"][1:]
    assert (
        ap["ativa"] is False
        and "conferência" in ap["motivo"]
        and ap["abrangencia"] == "estadual"
        and ap["uf"] == "AP"
    )
    assert (
        pel["abrangencia"] == "municipal"
        and pel["municipio"] == "Pelotas"
        and pel["sondagem"]["confianca"] == "baixa"
    )
    assert "# v4: novas fontes" in novo and novo.index("slug: pelotas-rs") < novo.index(
        "# v4: novas fontes"
    ), "entra no fim das instâncias"
    arq = tmp_path / "f.yaml"
    arq.write_text(novo, encoding="utf-8")
    instancias, _ = carregar_fontes(arq)
    assert [i.slug for i in instancias] == ["ce", "ap", "pelotas-rs"] and not instancias[1].ativa and instancias[2].sondagem["origem"] == "padrao"  # fmt: skip


def test_slug_nao_colide_com_existente():
    s = dm.Sondagem(dm.Candidato("mapacultural.ap.gov.br", "estado", "AP"), "mapas")
    usados = {"ap"}
    assert dm.bloco_yaml(s, HOJE, usados)[0] == "ap-2" and "ap-2" in usados


def test_corpo_do_pr_tem_tabela_e_passos_de_ativacao():
    s = dm.Sondagem(
        dm.Candidato("mapacultural.ap.gov.br", "estado", "AP"),
        "mapas",
        robots="permitido",
        confianca="alta",
        amostra={"name": "Edital X"},
    )
    corpo = dm.corpo_do_pr([s], HOJE, {"mapas": 1, "nao_mapas": 4})
    assert "| Estado (AP) | https://mapacultural.ap.gov.br | alta | permitido | Edital X |" in corpo
    assert (
        "Nada foi coletado" in corpo and "robots.txt" in corpo and "mapas: 1, nao_mapas: 4" in corpo
    )


def test_principal_dry_run_nao_altera_nada_e_real_grava_fontes_cache_e_pr(tmp_path, monkeypatch):
    fontes = tmp_path / "fontes.yaml"
    fontes.write_text(FONTES_MINI, encoding="utf-8")
    cache = tmp_path / "sondados.json"
    roteador = {"mapacultural.ap.gov.br": mapas()}
    monkeypatch.setattr(dm, "resolver_dns", dns_falso(set(roteador)))
    monkeypatch.setattr(dm, "REFERENCIA", tmp_path)
    (tmp_path / "ufs_capitais.json").write_text(json.dumps({"ufs": UFS}), encoding="utf-8")
    (tmp_path / "municipios_50k.json").write_text(
        json.dumps({"municipios": MUNICIPIOS}), encoding="utf-8"
    )
    monkeypatch.setattr(dm, "SNAPSHOT", tmp_path / "nao_existe.json")
    cliente, _ = web(roteador)
    monkeypatch.setattr(dm, "ClienteWeb", lambda: cliente)
    # descobrir() usa o resolvedor padrão por valor; troca direto no módulo
    original = dm.descobrir

    async def descobrir_com_dns(*a, **k):
        return await original(*a, **{**k, "resolvedor": dns_falso(set(roteador))})

    monkeypatch.setattr(dm, "descobrir", descobrir_com_dns)
    corpo = tmp_path / "pr.md"
    args = ["--fontes", str(fontes), "--sondados", str(cache), "--corpo-pr", str(corpo)]
    assert rodar(dm.principal(args + ["--dry-run"])) == 0
    assert (
        fontes.read_text(encoding="utf-8") == FONTES_MINI
        and not cache.exists()
        and not corpo.exists()
    )
    cliente2, _ = web(roteador)
    monkeypatch.setattr(dm, "ClienteWeb", lambda: cliente2)
    assert rodar(dm.principal(args)) == 0
    assert "slug: ap\n" in fontes.read_text(
        encoding="utf-8"
    ) and "mapacultural.ap.gov.br" in corpo.read_text(encoding="utf-8")
    assert (
        json.loads(cache.read_text(encoding="utf-8"))["hosts"]["mapacultural.ap.gov.br"][
            "resultado"
        ]
        == "proposto"
    )


def test_workflow_semanal_so_propoe_e_nao_ativa_nada():
    texto = (RAIZ / ".github" / "workflows" / "descobrir_mapas.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(texto)
    assert wf[True]["schedule"][0]["cron"] and "workflow_dispatch" in wf[True]
    assert "python -m scripts.descobrir_mapas" in texto and "ativa: true" not in texto
    assert wf["jobs"]["descobrir"]["timeout-minutes"] <= 30


# ── ativação das instâncias descobertas ──────────────────────────────────────────────────────


def test_toda_instancia_ativa_tem_termos_conferidos_e_robots_nao_proibido():
    instancias, _ = carregar_fontes()
    ativas = [i for i in instancias if i.ativa]
    assert len(ativas) >= 30
    for i in [
        x for x in ativas if x.sondagem
    ]:  # as descobertas automaticamente só ligam depois de conferidas
        assert i.termos_conferidos_em and i.termos_url, (
            f"{i.slug}: instância ativa sem termos conferidos"
        )
    for i in instancias:
        if i.sondagem and i.ativa:
            assert i.sondagem["robots"] == "permitido", i.slug
        if not i.ativa:
            assert i.motivo, f"{i.slug}: desativada sem motivo"


def test_site_sem_termos_publicados_deixa_a_observacao_registrada():
    instancias, _ = carregar_fontes()
    sem_pagina = [
        i
        for i in instancias
        if i.sondagem and i.ativa and i.termos_obs and "não publica termos" in i.termos_obs
    ]
    assert {i.slug for i in sem_pagina} >= {
        "maua-sp",
        "pelotas-rs",
        "arapiraca-al",
        "gravatai-rs",
        "itatiba-sp",
        "ribeiraopires-sp",
    }


def test_site_com_content_signal_ai_train_no_nunca_vai_para_o_llm():
    from worker.modelos import Edital
    from worker.scraper_worker import alvo_do_llm

    instancias, _ = carregar_fontes()
    assert [i.slug for i in instancias if i.sem_llm] == ["lagoasanta-mg"]

    def ed(fonte):
        return Edital.model_validate({
            "fonte": fonte, "id_externo": "1", "titulo": "Edital de teste de fomento", "orgao_fomento": "Org", "url_origem": "https://x.test/1",
            "inscricao_fim": "2099-01-01T00:00:00+00:00", "ambito": "brasil", "abrangencia": "municipal", "uf": "MG",
        })  # fmt: skip

    todos = [
        ed("mapas:lagoasanta-mg"),
        ed("mapas:quixada-ce"),
        ed("mapas:ce"),
        ed("mapas:falhou"),
        ed("curadoria"),
    ]
    alvo = alvo_do_llm(todos, {"mapas:falhou"}, instancias)
    assert [e.fonte for e in alvo] == ["mapas:quixada-ce", "mapas:ce"]


def test_pareceristas_nao_entram_como_edital_de_fomento():
    import re

    from worker.scraper_worker import carregar_fontes as cf

    _, params = cf()
    assert any(
        re.search(p, "credenciamento de pareceristas projetos pnab")
        for p in params.excluir_titulo_regex
    )
    assert not any(re.search(p, "credenciamento de artistas") for p in params.excluir_titulo_regex)
