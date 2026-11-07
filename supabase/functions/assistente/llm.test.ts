import assert from "node:assert/strict";
import { criarProvedor, ErroLlm, esquemaParaGemini, MODELO_PADRAO_GEMINI, ProvedorGemini } from "./llm.ts";

const env = (o: Record<string, string>) => ({ get: (k: string) => o[k] });
const resposta = (corpo: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(corpo), { status }));

Deno.test("Gemini: formato da requisição (chave no cabeçalho, JSON estruturado)", async () => {
  let visto: { url: string; init: RequestInit } | null = null;
  const fake = ((url: string, init: RequestInit) => {
    visto = { url, init };
    return resposta({ candidates: [{ content: { parts: [{ text: '{"ok":1}' }] } }] });
  }) as unknown as typeof fetch;
  const p = new ProvedorGemini("CHAVE-SECRETA", "gemini-x", fake);
  const r = await p.gerar("oi", { sistema: "sis", schema: { type: "object", properties: { a: { type: "string" } } } });
  assert.equal(r.texto, '{"ok":1}');
  assert.ok(visto!.url.includes("/models/gemini-x:generateContent"));
  assert.ok(!visto!.url.includes("CHAVE-SECRETA"), "a chave não pode ir na URL");
  assert.equal((visto!.init.headers as Record<string, string>)["x-goog-api-key"], "CHAVE-SECRETA");
  const corpo = JSON.parse(String(visto!.init.body));
  assert.equal(corpo.generationConfig.responseMimeType, "application/json");
  assert.equal(corpo.generationConfig.responseSchema.type, "OBJECT");
  assert.equal(corpo.generationConfig.responseSchema.properties.a.type, "STRING");
  assert.equal(corpo.systemInstruction.parts[0].text, "sis");
});

Deno.test("Gemini: erros viram tipos tratáveis", async () => {
  const tipoDe = async (f: () => Promise<Response>) => {
    try {
      await new ProvedorGemini("k", "m", f as unknown as typeof fetch).gerar("x", { sistema: "s" });
    } catch (e) {
      return (e as ErroLlm).tipo;
    }
    return "nenhum";
  };
  assert.equal(await tipoDe(() => resposta({}, 429)), "cota");
  assert.equal(await tipoDe(() => resposta({}, 503)), "indisponivel");
  assert.equal(await tipoDe(() => resposta({}, 400)), "requisicao");
  assert.equal(await tipoDe(() => resposta({ promptFeedback: { blockReason: "SAFETY" } })), "bloqueado");
  assert.equal(await tipoDe(() => resposta({ candidates: [] })), "indisponivel");
  assert.equal(await tipoDe(() => Promise.reject(Object.assign(new Error("x"), { name: "AbortError" }))), "timeout");
  assert.equal(await tipoDe(() => Promise.reject(new Error("rede"))), "indisponivel");
});

Deno.test("provedor e modelo vêm só do ambiente", async () => {
  assert.ok(criarProvedor(env({ GEMINI_API_KEY: "k" })) instanceof ProvedorGemini);
  assert.equal(MODELO_PADRAO_GEMINI, "gemini-3.6-flash");
  let modelo = "";
  const fake = ((url: string) => {
    modelo = url;
    return resposta({ candidates: [{ content: { parts: [{ text: "{}" }] } }] });
  }) as unknown as typeof fetch;
  await criarProvedor(env({ GEMINI_API_KEY: "k", LLM_MODEL: "outro-modelo" }), fake).gerar("x", { sistema: "s" });
  assert.ok(modelo.includes("outro-modelo"));
  await assert.rejects(criarProvedor(env({ LLM_PROVIDER: "groq", GROQ_API_KEY: "g" })).gerar("x", { sistema: "s" }), /não implementado/);
  assert.throws(() => criarProvedor(env({})), /GEMINI_API_KEY/);
  assert.throws(() => criarProvedor(env({ LLM_PROVIDER: "xyz" })), /desconhecido/);
});

Deno.test("esquemaParaGemini põe os tipos em maiúsculas sem tocar em enum", () => {
  const s = esquemaParaGemini({ type: "object", properties: { t: { type: "string", enum: ["a", "b"] } } }) as { type: string; properties: { t: { type: string; enum: string[] } } };
  assert.equal(s.type, "OBJECT");
  assert.deepEqual(s.properties.t.enum, ["a", "b"]);
});
