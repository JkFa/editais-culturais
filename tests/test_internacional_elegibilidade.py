"""Mapeamento de elegibilidade e custos: nada de 'aberto' sem a fonte dizer."""

import pytest

from worker.elegibilidade import classificar_elegibilidade, custos_cobertos


@pytest.mark.parametrize(
    "texto",
    [
        "This call is open to artists of all nationalities.",
        "Applications are welcome regardless of nationality.",
        "Open to artists from any country in the world.",
        "Applicants must have been trained in Africa, Asia, Latin America or the WANA region.",
        "The call is open to artists based in Brazil and Colombia.",
        "Convocatoria abierta a artistas de cualquier nacionalidad.",
        "Podrán participar artistas de América Latina.",
        "Podem participar artistas de qualquer nacionalidade.",
        "Países elegibles: Argentina, Brasil, Chile y Uruguay.",
    ],
)
def test_aberto_so_com_frase_explicita(texto):
    assert classificar_elegibilidade(texto) == "aberto"


@pytest.mark.parametrize(
    "texto",
    [
        None,
        "",
        "We invite artists to apply for a four-week residency in Frankfurt.",
        "A festival of experimental theatre in Rovigo, Italy.",
        "Research about Latin America and its archives.",  # menciona, mas não diz quem pode
        "Open to artists of all nationalities residing in Europe.",  # sinais conflitantes
        "Not open to artists from Latin America.",
        "Artists from Brazil are not eligible.",
        "Convocatoria para artistas de Colombia.",
    ],
)
def test_sem_frase_explicita_ou_com_conflito_e_verificar(texto):
    assert classificar_elegibilidade(texto) == "verificar"


def test_residencia_e_parceiro_so_quando_escritos():
    assert (
        classificar_elegibilidade("Applicants must be residents of Portugal.")
        == "requer_residencia"
    )
    assert classificar_elegibilidade("Applicants must reside in Germany.") == "requer_residencia"
    assert (
        classificar_elegibilidade("Projects require a partner organisation in the host country.")
        == "requer_parceiro"
    )
    assert (
        classificar_elegibilidade("Deben participar organizaciones socias de al menos dos países.")
        == "requer_parceiro"
    )


def test_instituicao_parceira_anfitria_nao_e_exigencia_de_parceiro():
    texto = "Residencies are hosted by six partner institutions across Europe."
    assert classificar_elegibilidade(texto) == "verificar"


def test_custos_so_com_verbo_de_cobertura_e_sem_negacao():
    assert custos_cobertos(
        "The Festival covers accommodation and gives a production budget, which will also cover travel costs."
    ) == ["viagem", "hospedagem", "producao"]
    assert custos_cobertos("Se cubren los gastos de viaje y alojamiento.") == [
        "viagem",
        "hospedagem",
    ]
    assert custos_cobertos("Inclui passagens e hospedagem.") == ["viagem", "hospedagem"]
    assert custos_cobertos("Travel and accommodation are the responsibility of the artist.") is None
    assert (
        custos_cobertos(
            "Support includes travel and accommodation arranged separately with the host."
        )
        is None
    )
    assert custos_cobertos("The residency does not cover travel.") is None
    assert custos_cobertos("Accommodation in Berlin is a great city.") is None
    assert custos_cobertos(None) is None
