"""A planilha .xlsx no LibreOffice de verdade: as fórmulas calculam e recalculam ao mudar uma quantidade."""

import csv
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DENO = shutil.which("deno") or str(Path.home() / ".deno" / "bin" / "deno")
SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")
pytestmark = pytest.mark.skipif(
    not SOFFICE or not Path(DENO).exists(), reason="LibreOffice ou Deno indisponível"
)


def gerar_e_calcular(tmp_path: Path, quantidade: int) -> dict[str, list[list[str]]]:
    xlsx = tmp_path / f"p{quantidade}.xlsx"
    subprocess.run(
        [DENO, "run", "--allow-read", "--allow-write", "--allow-env", "--allow-net", "--allow-sys",
         str(RAIZ / "tests" / "xlsx_gerar.ts"), str(xlsx), str(quantidade)],
        check=True, capture_output=True, timeout=180,
    )  # fmt: skip
    saida = tmp_path / f"csv{quantidade}"
    saida.mkdir()
    subprocess.run(
        [SOFFICE, "--headless", f"-env:UserInstallation=file://{tmp_path}/perfil",
         "--convert-to", "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,false,false,false,-1",
         "--outdir", str(saida), str(xlsx)],
        check=True, capture_output=True, timeout=180,
    )  # fmt: skip
    return {
        arq.stem.split("-", 1)[1]: list(csv.reader(arq.read_text(encoding="utf-8").splitlines()))
        for arq in saida.glob("*.csv")
    }


def numero(texto: str) -> float:
    return float(
        texto.replace("R$", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .replace(".", "")
        .replace(",", ".")
    )


def test_totais_recalculam_no_libreoffice_quando_a_quantidade_muda(tmp_path):
    antes = gerar_e_calcular(tmp_path, 2)
    depois = gerar_e_calcular(tmp_path, 5)
    assert set(antes) == {"Orçamento", "Resumo", "Cronograma", "Plano de mídia"}

    def orc(abas, rotulo):
        return next(linha for linha in abas["Orçamento"] if linha and linha[0] == rotulo)

    # total do item = Qtd × Valor, e o subtotal/total geral acompanham (SUBTOTAL não conta duas vezes)
    assert numero(antes["Orçamento"][5][6]) == 6000  # 2 × 3.000
    assert numero(depois["Orçamento"][5][6]) == 15000  # 5 × 3.000
    assert numero(orc(antes, "Subtotal · Produção")[6]) == 6000
    assert numero(orc(depois, "Subtotal · Produção")[6]) == 15000
    assert numero(orc(antes, "TOTAL GERAL")[6]) == 6000 + 1500 + 1000
    assert numero(orc(depois, "TOTAL GERAL")[6]) == 15000 + 1500 + 1000

    # Resumo: total por etapa (SUMIF entre abas), % e situação reagem à mudança
    def resumo(abas, etapa):
        return next(linha for linha in abas["Resumo"] if linha and linha[0] == etapa)

    assert numero(resumo(antes, "Produção")[1]) == 6000
    assert numero(resumo(depois, "Produção")[1]) == 15000
    assert numero(resumo(antes, "TOTAL")[1]) == 8500
    assert numero(resumo(depois, "TOTAL")[1]) == 17500
    pct_antes = float(resumo(antes, "Divulgação")[2].rstrip("%").replace(",", ".")) / 100
    pct_depois = float(resumo(depois, "Divulgação")[2].rstrip("%").replace(",", ".")) / 100
    assert abs(pct_antes - 1500 / 8500) < 0.001 and abs(pct_depois - 1500 / 17500) < 0.001
    assert resumo(antes, "Divulgação")[5] == "Acima do limite"  # 17,6% > 15%
    assert resumo(depois, "Divulgação")[5] == "OK"  # 8,6% <= 15%

    def do_resumo(abas, comeco):
        return next(x for x in abas["Resumo"] if x and x[0].startswith(comeco))[1]

    assert do_resumo(antes, "Situação do total") == "Dentro do teto"  # teto 12.000
    assert do_resumo(depois, "Situação do total") == "Acima do teto"
    assert numero(do_resumo(antes, "Diferença")) == 12000 - 8500
    assert numero(do_resumo(depois, "Diferença")) == 12000 - 17500

    # plano de mídia: custo da peça ligada vem do orçamento
    assert numero(antes["Plano de mídia"][5][4]) == 1500
