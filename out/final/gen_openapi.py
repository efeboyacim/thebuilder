import json, sys

S = lambda **p: {"type": "object", "properties": p}
STR = {"type": "string"}; NUM = {"type": "number"}; INT = {"type": "integer"}; BOOL = {"type": "boolean"}
CALL = S(ani=STR, lang=STR)
RESULT = S(guid=STR, returnCode=STR, messageText=STR, code=STR, message=STR, type=STR,
           details=S(guid=STR, rootCause=STR, errorOrigin=STR))
ERR = {"type": "object", "required": ["result"], "properties": {"result": RESULT}}
DEBT = S(statementPaymentStatus={"type": "string", "enum": ["HAS_DEBT", "NO_DEBT"]}, transferAmount=NUM, statementDebt=NUM,
         minPayment=NUM, remainingDebt=NUM, remainingMinPayment=NUM, currentPeriodExpense=NUM, totalDebt=NUM,
         totalDebtPaymentStatus={"type": "string", "enum": ["HAS_DEBT", "NO_DEBT"]}, remainAutomaticPaymentAmount=NUM)


def op(opid, summary, req_props, required, resp_data, errors):
    body = {"type": "object", "required": ["data"], "properties": {
        "data": {"type": "object", "required": required, "properties": req_props}, "call": CALL}}
    responses = {"200": {"description": "Success (result.returnCode = '1')", "content": {"application/json": {"schema": {
        "type": "object", "properties": {"data": resp_data, "result": RESULT}}}}}}
    for code, desc in errors:
        responses[str(code)] = {"description": desc, "content": {"application/json": {"schema": ERR}}}
    return {"post": {"operationId": opid, "summary": summary,
                     "requestBody": {"required": True, "content": {"application/json": {"schema": body}}},
                     "responses": responses}}


E400 = (400, "INVALID_ARGUMENTS_ERROR: required field missing/empty, or unknown record in test")
E500 = (500, "FBE00099 technical error")
spec = {
    "openapi": "3.0.3",
    "info": {"title": "Freya Bank voice statement services", "version": "2026-10-01"},
    "servers": [{"url": "https://freya-mock.efe-byci.workers.dev/voice/v1"}],
    "paths": {
        "/cards.list": op("cardsList", "Eligible credit cards of the customer",
                          {"customerNo": {"type": "string", "description": "string or number"}, "aiFlow": {"type": "string", "example": "CardOps"}},
                          ["customerNo"],
                          S(cards={"type": "array", "items": S(cardNumber=STR, cardNumberLast4=STR, productName=STR, status=STR, isMainCard=BOOL)},
                            validCardsCount=INT, exitState={"type": "string", "enum": ["1_CARD", "MULTI_CARD"]}),
                          [E400, (404, "FB_IVR_CHANNEL_102 WARNING: customer has no valid card"), E500]),
        "/cards.balance": op("cardsBalance", "Statement debt per currency of one card",
                             {"cardNumber": {"type": "string", "description": "exactly as cards.list returned it"}}, ["cardNumber"],
                             S(cardNumber=STR, cardNumberLast4=STR, productName=STR, statementDate=STR, lastPayDate=STR,
                               nextStatementDate=STR, nextLastPayDate=STR, debtTL=DEBT, debtUSD=DEBT, debtEUR=DEBT, debtGBP=DEBT,
                               statementPaymentStatus=STR, totalDebtPaymentStatus=STR, automaticPaymentFlag={"type": "string", "enum": ["Y", "N"]}),
                             [E400, (404, "FB_IVR_CHANNEL_120 card not found"), E500]),
        "/statement.week.get": op("statementWeekGet", "Current statement week of a card",
                                  {"cardNumber": STR}, ["cardNumber"],
                                  S(cardNumber=STR, cardNumberLast4=STR, productName=STR, statementWeek=INT, statementDay=INT,
                                    statementDate=STR, lastPayDate=STR, nextStatementDate=STR, nextLastPayDate=STR,
                                    relationType={"type": "string", "enum": ["", "M", "C"]}, relatedCardNumber=STR, relatedCardProductName=STR),
                                  [E400, (404, "FB_IVR_CHANNEL_121 card not found"), E500]),
        "/statement.week.decide": op("statementWeekDecide", "Preview of the dates for a new week (changes nothing)",
                                     {"cardNumber": STR, "newStatementWeek": {"type": "string", "enum": ["1", "2", "3", "4"]}, "statementDay": INT},
                                     ["cardNumber", "newStatementWeek", "statementDay"],
                                     S(statementDate=STR, statementDueDate=STR, statementDay=INT, responseCode=INT, responseMessage=STR),
                                     [E400, E500]),
        "/statement.week.change": op("statementWeekChange", "Change the statement week",
                                     {"cardNumber": STR, "newStatementWeek": {"type": "string", "enum": ["1", "2", "3", "4"]}, "statementDay": INT},
                                     ["cardNumber", "newStatementWeek", "statementDay"],
                                     S(responseCode=INT, responseMessage=STR, statementWeek=STR, statementDay=STR, exitState=STR),
                                     [E400, (401, "token missing/expired"), E500]),
        "/contact.email.get": op("contactEmailGet", "Registered e-mail / phone",
                                 {"customerNo": STR, "intAddressInfoType": {"type": "string", "enum": ["E", "P", "B"]}},
                                 ["customerNo", "intAddressInfoType"],
                                 S(communicationType=STR, ibanCountryCode=STR, ibanAreaCode=STR, ibanPhoneNum=STR, ibanEmail=STR,
                                   ttsEmail=STR, commGramType=STR, exitState=STR),
                                 [E400, E500]),
        "/statement.pdf.send": op("statementPdfSend", "E-mail the latest statement PDF",
                                  {"customerNo": STR, "cardNumber": STR, "currencyType": {"type": "string", "enum": ["TL", "USD", "EUR", "GBP"]},
                                   "statementDate": {"type": "string", "description": "dd.MM.yyyy from cards.balance"}, "emailAddress": STR},
                                  ["customerNo", "cardNumber", "currencyType", "statementDate", "emailAddress"],
                                  S(exitState={"type": "string", "example": "DONE"}),
                                  [E400, (404, "FB_IVR_CHANNEL_107 no PDF document"), E500]),
    },
}
with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
    json.dump(spec, f, ensure_ascii=False, indent=1)
