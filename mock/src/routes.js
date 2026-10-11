// Routes of the bank's services: POST /voice/v1/<service name>, e.g. POST /voice/v1/cards.list
// (service contract 2026-10-01; body {"data": {...}, "call": {...}}, response {"data", "result"}).
const SERVICES = [
  ["cards.list", false, false],
  ["cards.balance", true, false],
  ["statement.week.get", true, false],
  ["statement.week.decide", true, true],
  ["statement.week.change", true, true],
  ["contact.email.get", false, false],
  ["statement.pdf.send", false, false],
];

export const ROUTES = SERVICES.map(([service, byCard, byWeek]) => ({
  service, byCard, byWeek, method: "POST", path: "/voice/v1/" + service,
}));

// Request field names searched (path params, query, body at any depth) for each lookup key.
export const FIELDS = {
  customer: ["customerNo", "customerNumber"],
  card: ["cardNumber"],
  week: ["newStatementWeek", "statementWeek"],
};

const err = (http, code, message) => ({ http, response: { result: { guid: "00000000000000000000000000000000",
  returnCode: code, messageText: message, code, message, type: "ERROR",
  details: { guid: "00000000000000000000000000000000", rootCause: null, errorOrigin: null } } } });

export const NOT_FOUND = {
  customer: err(404, "FB_IVR_CHANNEL_102", "Müşteriye ait geçerli kart bilgisi bulunamadı."),
  service: err(404, "FB_IVR_CHANNEL_100", "Kayıt bulunamadı."),
  card: err(404, "FB_IVR_CHANNEL_103", "Kart bulunamadı."),
  week: err(400, "FB_IVR_CHANNEL_104", "Geçersiz hesap kesim haftası."),
};
