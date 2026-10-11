// Freya Bank service mock (Cloudflare Worker), after the service contract of 2026-10-01 and the test environment
// (test-customers.md/json). Every answer, HTTP status included, comes from data/test-customers.json; a customer,
// card or week without an entry gets the contract's error body. Token, headers, `call` and `aiFlow` are not checked.
import DATA from "../data/test-customers.json";
import { SERVICES, PREFIX, FIELD_WORDS } from "./routes.js";

const CUSTOMERS = Object.fromEntries(DATA.customers.map((c) => [String(c.customerNo), c]));
// Card numbers are unique across the test customers: card -> customer, for services called with a card only.
const CARD_OWNER = {};
for (const c of DATA.customers)
  for (const s of Object.values(c.services))
    if (s && typeof s === "object" && !("http" in s)) for (const k of Object.keys(s)) if (k.includes("*")) CARD_OWNER[k] = c;
const LOG = [];
const CORS = { "Access-Control-Allow-Origin": "*" };

const json = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", ...CORS } });
const guid = () => crypto.randomUUID().replace(/-/g, "");
const digits = (s) => String(s ?? "").replace(/\D/g, "");
const empty = (v) => v === undefined || v === null || String(v).trim() === "";
// The platform's "Test API Request" button sends "{{var}}" unfilled (cloudflare-mock README).
const isTemplate = (v) => typeof v === "string" && (v.startsWith("{{") || v.startsWith("%7B%7B"));

function error(http, code, message, type, rootCause) {
  const g = guid();
  return { http, response: { result: { guid: g, returnCode: code, messageText: message, code, message, type,
    details: { guid: g, rootCause, errorOrigin: "freya-mock" } } } };
}
const badArgs = (msg) => error(400, "INVALID_ARGUMENTS_ERROR", msg, "ERROR", "ValidationException");
const notFound = (svc) => svc.notFound
  ? error(404, svc.notFound[0], svc.notFound[1], "WARNING", "NotFoundException")
  : badArgs("record not found");

// Masked keys keep the first six and last four digits; match on exactly those (never on last four alone).
function cardKey(table, card) {
  if (table[card]) return card;
  const d = digits(card);
  if (d.length < 10) return null;
  return Object.keys(table).find((k) => { const kd = digits(k); return kd.slice(0, 6) === d.slice(0, 6) && kd.slice(-4) === d.slice(-4); }) || null;
}

function answer(name, data) {
  const svc = SERVICES[name];
  for (const f of svc.required) if (empty(data[f])) return badArgs(`${FIELD_WORDS[f] || f} cannot be empty`);
  const template = Object.values(data).some(isTemplate);
  let cust = null;
  if (!empty(data.customerNo)) cust = isTemplate(data.customerNo) ? DATA.customers[0] : CUSTOMERS[digits(data.customerNo)];
  if (!cust && svc.byCard && !empty(data.cardNumber)) {
    const k = cardKey(CARD_OWNER, String(data.cardNumber));
    cust = k ? CARD_OWNER[k] : (template ? DATA.customers[0] : null);
  }
  if (!cust) return notFound(svc);
  let entry = cust.services[name];
  if (!entry) return notFound(svc);
  if (svc.byCard) {
    const k = isTemplate(data.cardNumber) ? Object.keys(entry)[0] : cardKey(entry, String(data.cardNumber));
    if (!k) return notFound(svc);
    entry = entry[k];
  }
  if (svc.byWeek) {
    const w = isTemplate(data.newStatementWeek) ? Object.keys(entry)[0] : String(digits(data.newStatementWeek));
    if (!entry[w]) return notFound(svc);
    entry = entry[w];
  }
  return entry;
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS")
      return new Response(null, { headers: { ...CORS, "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*" } });
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health"))
      return json(200, { ok: true, customers: DATA.customers.length, services: Object.keys(SERVICES).map((s) => "POST " + PREFIX + s) });
    if (request.method === "GET" && url.pathname === "/_log") return json(200, LOG.slice(-50));
    if (request.method === "POST" && url.pathname === "/oauth2/token")
      return json(200, { access_token: "mock-" + guid(), token_type: "Bearer", expires_in: 1800 });
    const name = url.pathname.startsWith(PREFIX) ? url.pathname.slice(PREFIX.length) : null;
    if (request.method !== "POST" || !name || !SERVICES[name])
      return json(404, error(404, "NOT_FOUND", `no service ${request.method} ${url.pathname}`, "ERROR", "NoRoute").response);
    let body = {};
    try { body = await request.json(); } catch { body = {}; }
    const data = body && typeof body.data === "object" && body.data ? body.data : {};
    const e = answer(name, data);
    if (e.delaySeconds) await new Promise((r) => setTimeout(r, e.delaySeconds * 1000));
    LOG.push({ t: new Date().toISOString(), service: name, data, http: e.http });
    if (LOG.length > 200) LOG.shift();
    return json(e.http, e.response);
  },
};
