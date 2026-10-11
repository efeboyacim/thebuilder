// Freya Bank service mock (Cloudflare Worker). Data-driven: every answer, HTTP status included, comes from
// data/test-customers.json ("services" per customer, keyed by masked card number and requested week).
// Routes and request field names follow the bank's service contract: see routes.js.
import DATA from "../data/test-customers.json";
import { ROUTES, FIELDS, NOT_FOUND } from "./routes.js";

const CUSTOMERS = Object.fromEntries(DATA.customers.map((c) => [String(c.customerNo), c]));
const LOG = [];

const json = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "Access-Control-Allow-Origin": "*" } });

function matchPath(pattern, path) {
  const p = pattern.split("/").filter(Boolean), a = path.split("/").filter(Boolean);
  if (p.length !== a.length) return null;
  const params = {};
  for (let i = 0; i < p.length; i++) {
    if (p[i].startsWith("{") && p[i].endsWith("}")) params[p[i].slice(1, -1)] = decodeURIComponent(a[i]);
    else if (p[i] !== a[i]) return null;
  }
  return params;
}

// First value of any of `names`, searched in path params, query, then the body (any depth).
function find(names, sources) {
  for (const src of sources) {
    const stack = [src];
    while (stack.length) {
      const o = stack.shift();
      if (!o || typeof o !== "object") continue;
      for (const n of names) if (o[n] !== undefined && o[n] !== null && o[n] !== "") return String(o[n]);
      for (const v of Object.values(o)) if (v && typeof v === "object") stack.push(v);
    }
  }
  return null;
}

const digits = (s) => String(s || "").replace(/\D/g, "");
// The platform's "Test API Request" button sends "{{var}}" unfilled: answer with the first test customer.
const isTemplate = (v) => typeof v === "string" && (v.startsWith("{{") || v.startsWith("%7B%7B"));

// The services are keyed by the masked number from cards.list; accept masked, full or last-four forms.
function cardKey(table, card) {
  if (!card) return null;
  if (table[card]) return card;
  const keys = Object.keys(table);
  const d = digits(card);
  const last4 = d.slice(-4);
  const hits = keys.filter((k) => {
    const kd = digits(k);
    return kd.slice(-4) === last4 && (d.length <= 4 || d.startsWith(kd.slice(0, Math.min(6, kd.length - 4))));
  });
  return hits.length === 1 ? hits[0] : hits.length ? hits[0] : null;
}

function envelope(code, message) {
  return { result: { guid: crypto.randomUUID().replace(/-/g, ""), returnCode: code, messageText: message, code,
    message, type: "ERROR", details: { guid: null, rootCause: null, errorOrigin: null } } };
}

async function answer(route, params, url, body) {
  const sources = [params, Object.fromEntries(url.searchParams), body];
  const customerNo = find(FIELDS.customer, sources);
  const cust = isTemplate(customerNo) ? DATA.customers[0] : customerNo && CUSTOMERS[digits(customerNo)];
  if (!cust) return { http: NOT_FOUND.customer.http, response: NOT_FOUND.customer.response };
  let entry = cust.services[route.service];
  if (!entry) return { http: NOT_FOUND.service.http, response: NOT_FOUND.service.response };
  if (route.byCard) {
    const card = find(FIELDS.card, sources);
    const k = isTemplate(card) ? Object.keys(entry)[0] : cardKey(entry, card);
    if (!k) return { http: NOT_FOUND.card.http, response: NOT_FOUND.card.response };
    entry = entry[k];
  }
  if (route.byWeek) {
    const w = digits(find(FIELDS.week, sources));
    if (!w || !entry[w]) return { http: NOT_FOUND.week.http, response: NOT_FOUND.week.response };
    entry = entry[w];
  }
  return entry;
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS")
      return new Response(null, { headers: { "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "*" } });
    if (url.pathname === "/" || url.pathname === "/health")
      return json(200, { ok: true, customers: Object.keys(CUSTOMERS).length,
        routes: ROUTES.map((r) => `${r.method} ${r.path} -> ${r.service}`) });
    if (url.pathname === "/_log") return json(200, LOG.slice(-50));
    let body = {};
    if (request.method !== "GET" && request.method !== "HEAD") {
      try { body = await request.json(); } catch { body = {}; }
    }
    for (const route of ROUTES) {
      if (route.method !== request.method) continue;
      const params = matchPath(route.path, url.pathname);
      if (!params) continue;
      const e = await answer(route, params, url, body);
      if (e.delaySeconds) await new Promise((r) => setTimeout(r, e.delaySeconds * 1000));
      LOG.push({ t: new Date().toISOString(), service: route.service, path: url.pathname, body, http: e.http });
      if (LOG.length > 200) LOG.shift();
      return json(e.http, e.response);
    }
    return json(404, envelope("NOT_FOUND", `no route ${request.method} ${url.pathname}`));
  },
};

export { envelope };
