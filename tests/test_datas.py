from datetime import UTC, date, datetime

from worker.padronizacao import (
    fim_de_curadoria,
    inicio_de_curadoria,
    interpretar_fim,
    interpretar_inicio,
)


def api(data: str, fuso: str) -> dict:
    return {"date": data, "timezone_type": 3, "timezone": fuso}


def test_fim_com_horario_real_respeita_fuso_da_instancia():
    # Formato real do Mapas Culturais do Ceará (America/Fortaleza = UTC-3).
    fim, tem_horario = interpretar_fim(api("2026-11-12 22:00:00.000000", "America/Fortaleza"))
    assert tem_horario is True
    assert fim == datetime(2026, 11, 13, 1, 0, tzinfo=UTC)


def test_fim_so_data_vira_2359_local_sem_horario():
    fim, tem_horario = interpretar_fim(api("2026-11-06 23:59:00.000000", "Etc/GMT+3"))
    assert tem_horario is False
    assert fim == datetime(2026, 11, 7, 2, 59, 59, tzinfo=UTC)


def test_fim_meia_noite_e_tratado_como_so_data():
    fim, tem_horario = interpretar_fim(api("2026-11-16 00:00:00.000000", "America/Fortaleza"))
    assert tem_horario is False
    assert fim == datetime(2026, 11, 17, 2, 59, 59, tzinfo=UTC)


def test_fuso_diferente_de_brasilia_e_respeitado():
    fim, _ = interpretar_fim(api("2026-11-06 18:00:00.000000", "America/Manaus"))
    assert fim == datetime(2026, 11, 6, 22, 0, tzinfo=UTC)


def test_string_iso_sem_fuso_assume_sao_paulo():
    fim, tem_horario = interpretar_fim("2026-07-15T17:30:00")
    assert tem_horario is True
    assert fim == datetime(2026, 7, 15, 20, 30, tzinfo=UTC)


def test_string_iso_com_offset():
    fim, _ = interpretar_fim("2026-07-15T17:30:00-03:00")
    assert fim == datetime(2026, 7, 15, 20, 30, tzinfo=UTC)


def test_fuso_desconhecido_cai_em_sao_paulo():
    inicio = interpretar_inicio(api("2026-01-10 08:00:00.000000", "Fuso/Inexistente"))
    assert inicio == datetime(2026, 1, 10, 11, 0, tzinfo=UTC)


def test_valores_invalidos():
    assert interpretar_fim(None) is None
    assert interpretar_fim("") is None
    assert interpretar_fim("não é data") is None
    assert interpretar_inicio({"date": None}) is None


def test_curadoria_data_sem_horario():
    fim, tem_horario = fim_de_curadoria("2027-04-30")
    assert tem_horario is False
    assert fim == datetime(2027, 5, 1, 2, 59, 59, tzinfo=UTC)


def test_curadoria_data_com_horario_e_offset():
    fim, tem_horario = fim_de_curadoria("2027-04-30T17:59:00-03:00")
    assert tem_horario is True
    assert fim == datetime(2027, 4, 30, 20, 59, tzinfo=UTC)


def test_curadoria_aceita_objeto_date_do_yaml():
    fim, tem_horario = fim_de_curadoria(date(2027, 4, 30))
    assert (tem_horario, fim.day) == (False, 1)


def test_inicio_de_curadoria():
    assert inicio_de_curadoria("2026-03-30") == datetime(2026, 3, 30, 3, 0, tzinfo=UTC)
    assert inicio_de_curadoria(None) is None
