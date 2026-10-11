// Routes of the bank's services: POST /voice/v1/<service name>, e.g. POST /voice/v1/cards.list
// (service contract 2026-10-01; body {"data": {...}, "call": {...}}, response {"data", "result"}).
// byCard / byWeek: how test-customers.json keys the answers. required: data fields whose absence gives a 400.
// notFound: the service's 404 from contract section 4; null -> 400 INVALID_ARGUMENTS_ERROR (test-customers.md).
export const SERVICES = {
  "cards.list": { byCard: false, byWeek: false, required: ["customerNo"],
    notFound: ["FB_IVR_CHANNEL_102", "Müşteriye ait geçerli kart bilgisi bulunamadı."] },
  "cards.balance": { byCard: true, byWeek: false, required: ["cardNumber"],
    notFound: ["FB_IVR_CHANNEL_120", "Kart bakiye servisi hatası: kart bulunamadı"] },
  "statement.week.get": { byCard: true, byWeek: false, required: ["cardNumber"],
    notFound: ["FB_IVR_CHANNEL_121", "Hesap kesim servisi hatası: kart bulunamadı"] },
  "statement.week.decide": { byCard: true, byWeek: true, required: ["cardNumber", "newStatementWeek"], notFound: null },
  "statement.week.change": { byCard: true, byWeek: true, required: ["cardNumber", "newStatementWeek"], notFound: null },
  "contact.email.get": { byCard: false, byWeek: false, required: ["customerNo", "intAddressInfoType"], notFound: null },
  "statement.pdf.send": { byCard: false, byWeek: false,
    required: ["customerNo", "cardNumber", "currencyType", "statementDate", "emailAddress"],
    notFound: ["FB_IVR_CHANNEL_107", "Pdf dokümanı bulunamadı"] },
};

export const PREFIX = "/voice/v1/";

// Human-readable field names for the 400 message ("card number cannot be empty").
export const FIELD_WORDS = {
  customerNo: "customer number", cardNumber: "card number", newStatementWeek: "newStatementWeek",
  intAddressInfoType: "intAddressInfoType", currencyType: "currencyType", statementDate: "statementDate",
  emailAddress: "emailAddress",
};
