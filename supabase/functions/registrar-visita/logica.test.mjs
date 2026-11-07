import assert from "node:assert/strict";
import test from "node:test";
import {
  DESCONHECIDA, criarLimitador, ehBot, extrairIp, ipPublico, lerPagina, normalizarGeo,
  origemPermitida, semLocalizacao,
} from "./logica.mjs";

const cab = (obj) => new Headers(obj);

test("bots e user-agent ausente não contam", () => {
  assert.equal(ehBot(""), true);
  assert.equal(ehBot(undefined), true);
  assert.equal(ehBot("Mozilla/5.0 (compatible; Googlebot/2.1)"), true);
  assert.equal(ehBot("Mozilla/5.0 Chrome-Lighthouse"), true);
  assert.equal(ehBot("python-httpx/0.27"), true);
  assert.equal(ehBot("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15 Mobile Safari"), false);
});

test("DNT e GPC pedem contagem sem localização", () => {
  assert.equal(semLocalizacao(cab({ dnt: "1" })), true);
  assert.equal(semLocalizacao(cab({ "sec-gpc": "1" })), true);
  assert.equal(semLocalizacao(cab({})), false);
  assert.equal(semLocalizacao(cab({ dnt: "0" })), false);
});

test("extrai o primeiro IP do x-forwarded-for", () => {
  assert.equal(extrairIp(cab({ "x-forwarded-for": "203.0.113.9, 10.0.0.1" })), "203.0.113.9");
  assert.equal(extrairIp(cab({ "cf-connecting-ip": "198.51.100.7" })), "198.51.100.7");
  assert.equal(extrairIp(cab({})), null);
});

test("só IP público vai ao provedor de geolocalização", () => {
  for (const ip of ["127.0.0.1", "10.1.2.3", "192.168.0.5", "172.16.4.4", "::1", "fe80::1", null, "lixo!"]) {
    assert.equal(ipPublico(ip), false, String(ip));
  }
  assert.equal(ipPublico("203.0.113.9"), true);
  assert.equal(ipPublico("2001:db8::1"), true);
});

test("rate limit: 3 por minuto por IP e libera depois da janela", () => {
  let agora = 0;
  const permitir = criarLimitador({ maximo: 3, janelaMs: 60_000, agora: () => agora });
  assert.deepEqual([1, 2, 3, 4].map(() => permitir("a")), [true, true, true, false]);
  assert.equal(permitir("b"), true);
  agora = 61_000;
  assert.equal(permitir("a"), true);
});

test("geolocalização: Brasil guarda UF e cidade; exterior só o país; falha vira desconhecida", () => {
  assert.deepEqual(
    normalizarGeo({ success: true, country_code: "BR", region_code: "mg", city: " Ipatinga " }),
    { pais: "BR", uf: "MG", cidade: "Ipatinga" },
  );
  assert.deepEqual(
    normalizarGeo({ success: true, country_code: "PT", region_code: "11", city: "Lisboa" }),
    { pais: "PT", uf: "ZZ", cidade: "" },
  );
  assert.deepEqual(normalizarGeo({ success: false, message: "limite" }), DESCONHECIDA);
  assert.deepEqual(normalizarGeo(null), DESCONHECIDA);
  assert.equal(normalizarGeo({ success: true, country_code: "BR", region_code: "Minas", city: "x" }).uf, "ZZ");
});

test("origem e corpo", () => {
  assert.equal(origemPermitida("https://jkfa.github.io", ["https://jkfa.github.io"]), "https://jkfa.github.io");
  assert.equal(origemPermitida("https://outro.site", ["https://jkfa.github.io"]), null);
  assert.equal(lerPagina('{"pagina":"home"}'), "home");
  assert.equal(lerPagina('{"pagina":"admin"}'), null);
  assert.equal(lerPagina("não é json"), null);
});
