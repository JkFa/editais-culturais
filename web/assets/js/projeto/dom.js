/** `replaceChildren` que ignora null/false/undefined (o DOM puro escreveria a palavra "null"). */
export function preencher(no, ...filhos) {
  no.replaceChildren(...filhos.flat().filter((f) => f != null && f !== false));
}
