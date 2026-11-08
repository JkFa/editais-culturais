"""Placar de cobertura (worker/cobertura.py) e auditoria de recall (scripts/auditoria_cobertura.py)."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from scripts import auditoria_cobertura as aud
from worker import cobertura as cob
from worker.catalogo import FonteConfig, Territorio, carregar_fontes_v4
from worker.coletores.mapas_culturais import Instancia
from worker.persistencia import ConfigSupabase, Supabase
from worker.scraper_worker import FONTES, carregar_fontes

RAIZ = Path(__file__).resolve().parent.parent
AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
REFS_REAIS = cob.Referencias.carregar()


def refs_mini(auditoria=None):
    return cob.Referencias(
        ufs=[
            {"uf": "CE", "nome": "Ceará", "capital": "Fortaleza", "capital_ibge": 2304400},
            {"uf": "SP", "nome": "São Paulo", "capital": "São Paulo", "capital_ibge": 3550308},
        ],
        municipios=[
            {"ibge": 2304400, "nome": "Fortaleza", "uf": "CE", "populacao": 2400000},
            {"ibge": 3550308, "nome": "São Paulo", "uf": "SP", "populacao": 11000000},
            {"ibge": 3509502, "nome": "Campinas", "uf": "SP", "populacao": 1100000},
            {"ibge": 3548708, "nome": "São Bernardo", "uf": "SP", "populacao": 800000},
        ],
        auditoria=auditoria,
    )


def edital(i=1, **kw):
    base = {
        "chave": f"k{i}", "fonte": "mapas:ce", "id_externo": str(i), "titulo": f"Edital número {i} de fomento teatral {i * 7}",
        "uf": "CE", "ambito": "brasil", "orgao_fomento": "Secult", "abrangencia": "estadual", "inscricao_fim": (AGORA + timedelta(days=10)).isoformat(),
        "valor_total": None, "teto_por_projeto": None, "proponentes": [], "proveniencia": {}, "url_origem": f"https://x.test/{i}",
        "url_inscricao": None, "url_edital_pdf": None, "fontes_alternativas": [],
    }  # fmt: skip
    return {**base, **kw}


def fonte(id_, status="ok", ativa=True, ha_horas=1):
    return {
        "fonte": id_,
        "nome": id_,
        "ativa": ativa,
        "status": status,
        "url": f"https://{id_.replace(':', '-')}.test/",
        "ultimo_sucesso": (AGORA - timedelta(hours=ha_horas)).isoformat(),
    }


def inst(slug, abr, uf, mun=None, ativa=True):
    return Instancia(
        slug=slug,
        nome=slug,
        base_url=f"https://{slug}.test",
        abrangencia=abr,
        uf=uf,
        municipio=mun,
        ativa=ativa,
    )


def calc(editais, fontes, instancias, refs=None, **kw):
    snap = {"editais": editais, "fontes": fontes}
    return cob.calcular(
        snap,
        cob.territorios_declarados(instancias, kw.get("cfg", [])),
        refs or refs_mini(),
        AGORA,
        kw.get("anterior"),
    )


def m(c, id_):
    return next(x for x in c["metricas"] if x["id"] == id_)


# ── regras de base ───────────────────────────────────────────────────────────────────────────


def test_pesos_somam_dez_e_nota_maxima_exige_tudo():
    assert sum(cob.PESOS.values()) == pytest.approx(10.0)
    assert [x[0] for x in cob.METRICAS] == [
        "ufs",
        "capitais",
        "municipios",
        "recall",
        "valor",
        "prazo_proponentes",
        "frescor",
        "higiene",
    ]


def test_cenario_vazio_so_pontua_higiene_e_nada_inventa():
    c = calc([], [], [])
    assert c["nota"] == 1.0, "sem editais não há vencidos nem duplicatas: só a higiene pontua"
    assert m(c, "recall")["medido"] is False and m(c, "recall")["pontos"] == 0


def test_nota_perfeita_quando_todas_as_metas_batem():
    refs = refs_mini({"amostra": 60, "recall": 0.95, "data": "2026-11-01"})
    instancias = [inst("ce", "estadual", "CE"), inst("sp", "estadual", "SP"), inst("fortaleza", "municipal", "CE", "Fortaleza"),
                  inst("saopaulo", "municipal", "SP", "São Paulo"), inst("campinas", "municipal", "SP", "Campinas"), inst("sbc", "municipal", "SP", "São Bernardo")]  # fmt: skip
    fontes = [fonte(i.fonte) for i in instancias]
    eds = [edital(i, valor_total=1000, proponentes=["PF"], proveniencia={"valor_total": {"via": "regex", "trecho": "R$ 1.000"}, "proponentes": {"via": "regex", "trecho": "PF"}, "inscricao_fim": {"via": "regex", "trecho": "até"}}) for i in range(1, 6)]  # fmt: skip
    c = calc(eds, fontes, instancias, refs)
    assert [round(x["fracao"], 3) for x in c["metricas"]] == [1.0] * 8
    assert c["nota"] == 10.0


# ── territórios ──────────────────────────────────────────────────────────────────────────────


def test_so_fonte_estadual_saudavel_cobre_a_uf_federal_nao_conta():
    instancias = [
        inst("br", "estadual", None),
        inst("ce", "estadual", "CE"),
        inst("sp", "estadual", "SP"),
    ]
    fontes = [fonte("mapas:br"), fonte("mapas:ce"), fonte("mapas:sp", status="erro")]
    c = calc([], fontes + [fonte("cultbr-pnab")], instancias)
    assert m(c, "ufs")["atual"] == 1, "SP está com erro; a federal não tem UF"
    assert {u["uf"]: u["fonte_estadual"] for u in c["por_uf"]} == {"CE": 1.0, "SP": 0.0}


def test_fonte_desativada_ou_com_erro_nao_cobre_e_parcial_vale_meio():
    instancias = [inst("ce", "estadual", "CE", ativa=False)]
    c = calc([], [fonte("mapas:ce")], instancias)
    assert m(c, "ufs")["atual"] == 0, (
        "desativada no fontes.yaml não cobre, mesmo que apareça no status"
    )
    cfg = [
        FonteConfig(
            id="vigia-sp",
            nome="v",
            tipo="oficial",
            metodo="vigia",
            url="https://v.test",
            cobertura=Territorio(ente="capital", uf="SP", municipio="São Paulo", parcial=True),
        )
    ]
    c = calc([], [fonte("vigia-sp")], [], cfg=cfg)
    assert m(c, "capitais")["atual"] == 0.5 and m(c, "municipios")["atual"] == 0.5


def test_municipio_e_capital_casam_por_nome_sem_acento_e_uf():
    instancias = [
        inst("a", "municipal", "SP", "SAO paulo"),
        inst("b", "municipal", "SP", "Campinas"),
        inst("c", "municipal", "SP", "Cidade Pequena"),
        inst("d", "municipal", "SP", None),
    ]
    c = calc([], [fonte(i.fonte) for i in instancias], instancias)
    assert m(c, "capitais")["atual"] == 1 and m(c, "municipios")["atual"] == 2, (
        "município fora da lista e consórcio sem município não contam"
    )


def test_territorios_do_fontes_yaml_real():
    instancias, _ = carregar_fontes()
    cfg = carregar_fontes_v4(FONTES)
    t = {x.fonte: x for x in cob.territorios_declarados(instancias, cfg)}
    assert (t["mapas:ce"].ente, t["mapas:ce"].uf) == ("estado", "CE")
    assert (t["mapas:aracaju"].ente, t["mapas:aracaju"].municipio) == ("municipio", "Aracaju")
    assert "mapas:br" not in t and "mapas:cim-amfri" not in t and "mapas:iberculturaviva" not in t
    assert t["vigia-spcine-fomento"].parcial and t["vigia-rj-incentivo-cultura"].ente == "estado"


# ── recall, evidência, frescor, higiene ──────────────────────────────────────────────────────


def test_recall_sem_auditoria_ou_com_amostra_pequena_vale_zero():
    base = {"amostra": 10, "recall": 1.0, "data": "2026-11-01"}
    assert m(calc([], [], [], refs_mini(base)), "recall")["pontos"] == 0
    assert m(calc([], [], [], refs_mini({**base, "amostra": 30})), "recall")[
        "pontos"
    ] == pytest.approx(2.5)
    assert m(calc([], [], [], refs_mini({**base, "amostra": 40, "recall": 0.45})), "recall")[
        "pontos"
    ] == pytest.approx(1.25)


def test_valor_proponentes_e_prazo_so_contam_com_evidencia():
    prov = {"valor_total": {"via": "regex", "trecho": "R$ 5 mil"}}
    eds = [edital(1, valor_total=5000, proveniencia=prov), edital(2, valor_total=5000), edital(3, teto_por_projeto=900, proveniencia={"teto_por_projeto": {"via": "api"}}), edital(4, fonte="curadoria", inscricao_fim=AGORA.isoformat() + "")]  # fmt: skip
    eds[3]["inscricao_fim"] = (AGORA + timedelta(days=3)).isoformat()
    c = calc(eds, [], [])
    assert m(c, "valor")["atual"] == 2, "valor sem proveniência não conta"
    assert m(c, "prazo_proponentes")["prazo"] == 3, (
        "curadoria sem proveniência de prazo não conta; Mapas (API) conta"
    )
    assert m(c, "prazo_proponentes")["proponentes"] == 0


def test_internacional_fica_fora_da_conta():
    c = calc(
        [edital(1), edital(2, ambito="internacional", titulo="Open call Berlin residency")], [], []
    )
    assert c["editais_abertos"] == 1


def test_frescor_so_conta_ativas_ok_e_recentes():
    fontes = [
        fonte("a"),
        fonte("b", ha_horas=30),
        fonte("c", status="erro"),
        fonte("d", ativa=False),
        fonte("e"),
    ]
    assert (
        m(calc([], fontes, []), "frescor")["atual"] == 2
        and m(calc([], fontes, []), "frescor")["total"] == 4
    )


def test_higiene_vencido_e_duplicata_tiram_20_por_cento_cada():
    vencido = edital(1, inscricao_fim=(AGORA - timedelta(hours=1)).isoformat())
    c = calc([vencido], [], [])
    assert m(c, "higiene")["fracao"] == 0.8 and m(c, "higiene")["vencidos"] == [vencido["titulo"]]
    a = edital(
        2,
        fonte="mapas:ce",
        titulo="Edital de Fomento às Artes Cênicas Cearenses",
        url_origem="https://a.test/1",
    )
    b = edital(
        3,
        fonte="cultbr-pnab",
        titulo="Edital de Fomento às Artes Cênicas Cearenses 2026",
        url_origem="https://b.test/9",
    )
    c = calc([a, b], [], [])
    assert len(m(c, "higiene")["duplicatas"]) == 1 and m(c, "higiene")["fracao"] == 0.8
    assert calc([vencido] * 1 + [edital(i) for i in range(2, 4)] + [edital(9, inscricao_fim=(AGORA - timedelta(days=1)).isoformat())] * 4, [], [])["metricas"][-1]["fracao"] == 0.0  # fmt: skip


# ── histórico e arquivos ─────────────────────────────────────────────────────────────────────


def test_dias_seguidos_exige_dias_consecutivos_terminando_hoje():
    h = [{"data": f"2026-11-0{d}", "nota": 9.2} for d in range(1, 6)]
    assert cob.dias_seguidos(h, 9.0) == 5
    assert cob.dias_seguidos(h + [{"data": "2026-11-06", "nota": 8.0}], 9.0) == 0
    assert cob.dias_seguidos([h[0], h[2], h[3]], 9.0) == 2, (
        "buraco no calendário interrompe a sequência"
    )
    assert cob.dias_seguidos([], 9.0) == 0


def test_gerar_grava_json_badge_e_acumula_historico(tmp_path):
    snap = {"editais": [edital(1)], "fontes": [fonte("mapas:ce")]}
    instancias = [inst("ce", "estadual", "CE")]
    saida = tmp_path / "cobertura.json"
    primeiro = cob.gerar(
        snap, instancias, [], AGORA - timedelta(days=1), saida=saida, refs=refs_mini()
    )
    segundo = cob.gerar(snap, instancias, [], AGORA, saida=saida, refs=refs_mini())
    segundo_de_novo = cob.gerar(snap, instancias, [], AGORA, saida=saida, refs=refs_mini())
    assert [h["data"] for h in segundo_de_novo["historico"]] == ["2026-11-07", "2026-11-08"], (
        "regravar o mesmo dia não duplica"
    )
    assert (
        json.loads(saida.read_text(encoding="utf-8"))["nota"] == segundo["nota"] == primeiro["nota"]
    )
    badge = json.loads((tmp_path / "cobertura-badge.json").read_text(encoding="utf-8"))
    assert (
        badge["schemaVersion"] == 1
        and badge["label"] == "cobertura"
        and badge["message"].endswith("/10")
        and "," in badge["message"]
    )


def test_referencias_ibge_versionadas_estao_completas():
    ufs = REFS_REAIS.ufs
    assert (
        len(ufs) == 27
        and len({u["uf"] for u in ufs}) == 27
        and len({u["capital_ibge"] for u in ufs}) == 27
    )
    assert {u["capital"] for u in ufs} >= {"Brasília", "Rio de Janeiro", "São Paulo", "Palmas"}
    grandes = REFS_REAIS.municipios
    assert 300 <= len(grandes) <= 340 and all(g["populacao"] > 100_000 for g in grandes)
    assert all(u["capital_ibge"] in {g["ibge"] for g in grandes} for u in ufs), (
        "toda capital tem mais de 100 mil habitantes"
    )
    assert grandes[0]["nome"] == "São Paulo"


def test_nota_do_snapshot_real_e_reproduzivel_e_fica_entre_0_e_10(tmp_path):
    snap = json.loads((RAIZ / "web" / "data" / "snapshot.json").read_text(encoding="utf-8"))
    instancias, _ = carregar_fontes()
    cfg = carregar_fontes_v4(FONTES)
    agora = datetime.fromisoformat(snap["gerado_em"])
    a = cob.gerar(snap, instancias, cfg, agora, saida=tmp_path / "a.json")
    b = cob.gerar(snap, instancias, cfg, agora, saida=tmp_path / "b.json")
    assert a["nota"] == b["nota"] and 0 <= a["nota"] <= 10
    assert m(a, "higiene")["fracao"] == 1.0, "o snapshot publicado não tem vencidos nem duplicatas"
    assert (
        len(a["por_uf"]) == 27
        and sum(u["abertos"] for u in a["por_uf"]) + a["editais_sem_uf"] == a["editais_abertos"]
    )


# ── Supabase ─────────────────────────────────────────────────────────────────────────────────


def test_upsert_cobertura_grava_uma_linha_por_dia():
    pedidos = []

    def tratar(req: httpx.Request) -> httpx.Response:
        pedidos.append(
            (
                req.method,
                req.url.path,
                dict(req.url.params),
                json.loads(req.content),
                req.headers["prefer"],
            )
        )
        return httpx.Response(201)

    import asyncio

    async def rodar():
        sb = Supabase(
            ConfigSupabase("https://sb.test", "chave"), transporte=httpx.MockTransport(tratar)
        )
        await sb.upsert_cobertura(
            {
                "data": "2026-11-08",
                "nota": 2.36,
                "metricas": [{"id": "x"}],
                "por_uf": [],
                "historico": [1],
            }
        )
        await sb.fechar()

    asyncio.run(rodar())
    metodo, caminho, params, corpo, prefer = pedidos[0]
    assert (metodo, caminho.rsplit("/", 1)[-1], params) == (
        "POST",
        "cobertura_diaria",
        {"on_conflict": "data"},
    )
    assert corpo == {"data": "2026-11-08", "nota": 2.36, "metricas": [{"id": "x"}], "por_uf": []}
    assert "merge-duplicates" in prefer


# ── auditoria de recall ──────────────────────────────────────────────────────────────────────

HOJE = date(2026, 11, 8)
SNAP = {
    "editais": [edital(1, titulo="Edital de Fomento às Artes Cênicas Cearenses", url_origem="https://secult.ce.gov.br/edital-1/", inscricao_fim="2026-12-01T02:59:59Z", fontes_alternativas=[{"url": "https://portal.cultbr.test/9"}])],
    "fontes": [{**fonte("mapas:ce"), "url": "https://mapacultural.secult.ce.gov.br"}, {**fonte("mapas:pe", status="erro"), "url": "https://mapacultural.pe.gov.br"}, {**fonte("bb-patrocinios", ativa=False), "url": "https://www.bb.com.br/patrocinios"}],
}  # fmt: skip


def item(titulo, url, **kw):
    return {"titulo": titulo, "url": url, "orgao": "Órgão", "uf": "CE", "prazo": "2026-12-01", **kw}


def test_auditoria_acha_por_url_canonica_alternativa_e_por_titulo_com_mesmo_prazo():
    assert aud.encontrado(
        item("qualquer", "http://www.secult.ce.gov.br/edital-1?utm_source=x#topo"), SNAP["editais"]
    )
    assert aud.encontrado(item("qualquer", "https://portal.cultbr.test/9"), SNAP["editais"]), (
        "URL de fonte alternativa também vale"
    )
    assert aud.encontrado(
        item("Fomento às Artes Cênicas Cearenses (edital completo)", "https://outro.test/a"),
        SNAP["editais"],
    )
    assert not aud.encontrado(
        item("Fomento às Artes Cênicas do Ceará", "https://outro.test/a", prazo="2027-03-01"),
        SNAP["editais"],
    ), "mesmo título com prazo diferente é outro edital"
    assert not aud.encontrado(
        item("Prêmio de literatura infantil", "https://outro.test/b"), SNAP["editais"]
    )


def test_auditoria_calcula_recall_so_com_itens_abertos_e_explica_a_causa():
    amostra = {"itens": [
        item("Edital de Fomento às Artes Cênicas Cearenses", "https://x.test/1"),
        item("Prêmio Literário do Ceará", "https://mapacultural.secult.ce.gov.br/oportunidade/77"),
        item("Chamada Pernambuco de Música", "https://mapacultural.pe.gov.br/oportunidade/5", uf="PE"),
        item("Patrocínio BB Cultural", "https://www.bb.com.br/patrocinios/x", uf=None),
        item("Edital da Bahia de Dança", "https://cultura.ba.gov.br/edital", uf="BA"),
        item("Edital vencido", "https://x.test/velho", prazo="2026-10-01"),
    ]}  # fmt: skip
    r = aud.auditar(amostra, SNAP, HOJE)
    assert (r["amostra"], r["encontrados"], r["recall"], r["descartados_por_prazo"]) == (
        5,
        1,
        0.2,
        1,
    )
    causas = {f["titulo"]: f["causa"] for f in r["faltantes"]}
    assert "parser falhou ou o filtro descartou" in causas["Prêmio Literário do Ceará"]
    assert "problema na última coleta" in causas["Chamada Pernambuco de Música"]
    assert "desativado" in causas["Patrocínio BB Cultural"]
    assert causas["Edital da Bahia de Dança"] == "fonte não monitorada"
    corpo = aud.corpo_da_issue(r)
    assert "1 de 5" in corpo and "menos de 30" in corpo and "| [Prêmio Literário do Ceará]" in corpo


def test_auditoria_main_grava_resultado_e_dry_run_nao_grava(tmp_path, capsys):
    amostra = tmp_path / "amostra.json"
    amostra.write_text(
        json.dumps(
            {
                "itens": [
                    item(
                        "Edital de Fomento às Artes Cênicas Cearenses",
                        "https://x.test/1",
                        prazo="2099-01-01",
                    )
                ]
            }
        ),
        encoding="utf-8",
    )
    snap = tmp_path / "snap.json"
    snap.write_text(
        json.dumps(
            {**SNAP, "editais": [{**SNAP["editais"][0], "inscricao_fim": "2099-01-01T02:59:59Z"}]}
        ),
        encoding="utf-8",
    )
    saida = tmp_path / "auditoria.json"
    assert (
        aud.main(
            ["--amostra", str(amostra), "--snapshot", str(snap), "--saida", str(saida), "--dry-run"]
        )
        == 0
        and not saida.exists()
    )
    assert (
        aud.main(["--amostra", str(amostra), "--snapshot", str(snap), "--saida", str(saida)]) == 0
    )
    assert json.loads(saida.read_text(encoding="utf-8"))["recall"] == 1.0
    assert aud.main(["--amostra", str(tmp_path / "nao_existe.json"), "--snapshot", str(snap)]) == 2


def test_amostra_versionada_tem_a_forma_certa():
    caminho = RAIZ / "worker" / "referencia" / "amostra_recall.json"
    if not caminho.exists():
        pytest.skip("amostra ainda não levantada")
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    assert dados["metodologia"]
    urls = [i["url"] for i in dados["itens"]]
    assert len(urls) == len(set(urls)), "itens repetidos inflariam o recall"
    for i in dados["itens"]:
        assert {"titulo", "url", "orgao", "uf", "prazo", "conferido_em"} <= set(i), i
        assert i["url"].startswith("https://") and "prosas.com.br" not in i["url"]
        assert i["uf"] is None or i["uf"] in cob.Referencias.carregar().ufs[0].keys() | {
            u["uf"] for u in REFS_REAIS.ufs
        }


def test_workflows_da_cobertura_e_commit_do_json():
    import yaml

    a = yaml.safe_load(
        (RAIZ / ".github" / "workflows" / "auditoria_cobertura.yml").read_text(encoding="utf-8")
    )
    assert a[True]["schedule"][0]["cron"] == "0 13 1 * *" and "workflow_dispatch" in a[True]
    assert a["permissions"] == {"contents": "write", "issues": "write"}
    assert "web/data/cobertura.json" in (
        RAIZ / ".github" / "workflows" / "sync_editais.yml"
    ).read_text(encoding="utf-8")
