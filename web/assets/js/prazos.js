// Regras do badge de prazo. Todo cálculo usa o calendário de Brasília.
import { formatarHora, partesBrasilia, plural } from "./formatos.js";

function diaCivil(dataOuIso) {
  const p = partesBrasilia(dataOuIso);
  return Date.UTC(p.ano, p.mes - 1, p.dia) / 86_400_000;
}

/** Dias de calendário entre hoje e o dia do prazo final (0 = termina hoje). */
export function diasRestantes(fimIso, agora = new Date()) {
  return diaCivil(fimIso) - diaCivil(agora);
}

export function encerrado(edital, agora = new Date()) {
  return new Date(edital.inscricao_fim).getTime() < agora.getTime();
}

/** {tipo, texto} onde tipo ∈ encerrado | hoje | urgente | aberto. */
export function estadoDoPrazo(edital, agora = new Date()) {
  if (encerrado(edital, agora)) return { tipo: "encerrado", texto: "Encerrado" };
  const dias = diasRestantes(edital.inscricao_fim, agora);
  if (dias <= 0) {
    return {
      tipo: "hoje",
      texto: edital.fim_tem_horario
        ? `Encerra hoje às ${formatarHora(edital.inscricao_fim)}`
        : "Encerra hoje",
    };
  }
  if (dias <= 7) return { tipo: "urgente", texto: `Encerra em ${plural(dias, "dia", "dias")}` };
  return { tipo: "aberto", texto: `Aberto por mais ${plural(dias, "dia", "dias")}` };
}

/** Meses até a janela habitual abrir (0 = dentro dela). Cíclico, vira o ano. */
export function mesesAteJanela(inicio, fim, mesAtual) {
  if (inicio == null || fim == null) return null;
  const dentro = inicio <= fim ? mesAtual >= inicio && mesAtual <= fim : mesAtual >= inicio || mesAtual <= fim;
  return dentro ? 0 : (((inicio - mesAtual) % 12) + 12) % 12;
}

export function mesAtualBrasilia(agora = new Date()) {
  return partesBrasilia(agora).mes;
}
