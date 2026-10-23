"""Sample pain.001 / pain.008 messages for the sandbox demo originators (dated from today)."""
from __future__ import annotations

from datetime import date, timedelta

from ..db import utcnow
from ..domain.windows import ET


def _next_weekday(d: date, n: int = 1) -> date:
    d = d + timedelta(days=n)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _hdr(msg_id: str, n: int, total: str, initg: str, company_id: str) -> str:
    now = utcnow().astimezone(ET).isoformat(timespec="seconds")
    return f"""  <GrpHdr>
      <MsgId>{msg_id}</MsgId>
      <CreDtTm>{now}</CreDtTm>
      <NbOfTxs>{n}</NbOfTxs>
      <CtrlSum>{total}</CtrlSum>
      <InitgPty><Nm>{initg}</Nm><Id><OrgId><Othr><Id>{company_id}</Id></Othr></OrgId></Id></InitgPty>
    </GrpHdr>"""


def _cdt(e2e, amount, name, routing, acct, ctgy=None, ustrd=None, org=False, extra=""):
    pid = f"<Id><OrgId><Othr><Id>{e2e[-6:]}</Id></Othr></OrgId></Id>" if org else ""
    rmt = f"<RmtInf><Ustrd>{ustrd}</Ustrd></RmtInf>" if ustrd else ""
    pt = f"<PmtTpInf><CtgyPurp><Cd>{ctgy}</Cd></CtgyPurp></PmtTpInf>" if ctgy else ""
    return f"""      <CdtTrfTxInf>
        <PmtId><InstrId>{e2e}</InstrId><EndToEndId>{e2e}</EndToEndId></PmtId>{pt}
        <Amt><InstdAmt Ccy="USD">{amount}</InstdAmt></Amt>
        <CdtrAgt><FinInstnId><ClrSysMmbId><ClrSysId><Cd>USABA</Cd></ClrSysId><MmbId>{routing}</MmbId></ClrSysMmbId></FinInstnId></CdtrAgt>
        <Cdtr><Nm>{name}</Nm>{pid}<PstlAdr><Ctry>US</Ctry></PstlAdr></Cdtr>
        <CdtrAcct><Id><Othr><Id>{acct}</Id></Othr></Id></CdtrAcct>{extra}{rmt}
      </CdtTrfTxInf>"""


def pain001_payroll(suffix: str = "") -> str:
    today = utcnow().astimezone(ET).date()
    pay = _next_weekday(today, 2).isoformat()
    tag = utcnow().strftime("%Y%m%d%H%M%S")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09">
  <CstmrCdtTrfInitn>
{_hdr(f"ACME-PAY-{tag}{suffix}", 3, "6420.00", "Acme Payroll Services", "1111111111")}
    <PmtInf>
      <PmtInfId>PAYROLL-{pay}</PmtInfId>
      <PmtMtd>TRF</PmtMtd>
      <NbOfTxs>3</NbOfTxs>
      <CtrlSum>6420.00</CtrlSum>
      <PmtTpInf><SvcLvl><Cd>NURG</Cd></SvcLvl><CtgyPurp><Cd>SALA</Cd></CtgyPurp></PmtTpInf>
      <ReqdExctnDt><Dt>{pay}</Dt></ReqdExctnDt>
      <Dbtr><Nm>Acme Payroll Services</Nm><Id><OrgId><Othr><Id>1111111111</Id></Othr></OrgId></Id></Dbtr>
      <DbtrAcct><Id><Othr><Id>9000000001</Id></Othr></Id></DbtrAcct>
      <DbtrAgt><FinInstnId><ClrSysMmbId><ClrSysId><Cd>USABA</Cd></ClrSysId><MmbId>021000021</MmbId></ClrSysMmbId></FinInstnId></DbtrAgt>
{_cdt("EMP-1001", "2150.00", "Maria Lopez", "021000021", "5550001111", ustrd="Salary period ending " + pay)}
{_cdt("EMP-1002", "1980.00", "Daniel Brooks", "011000015", "4412009876")}
{_cdt("EMP-1003", "2290.00", "Wrong Routing Employee", "123456789", "1000200030")}
    </PmtInf>
  </CstmrCdtTrfInitn>
</Document>
"""


def pain001_vendor(suffix: str = "") -> str:
    today = utcnow().astimezone(ET).date()
    tag = utcnow().strftime("%Y%m%d%H%M%S")
    pay = _next_weekday(today, 1).isoformat()
    strd = """
        <RmtInf>
          <Strd><RfrdDocInf><Tp><CdOrPrtry><Cd>CINV</Cd></CdOrPrtry></Tp><Nb>INV-20481</Nb></RfrdDocInf><RfrdDocAmt><RmtdAmt Ccy="USD">12000.00</RmtdAmt></RfrdDocAmt></Strd>
          <Strd><RfrdDocInf><Tp><CdOrPrtry><Cd>CINV</Cd></CdOrPrtry></Tp><Nb>INV-20495</Nb></RfrdDocInf><RfrdDocAmt><RmtdAmt Ccy="USD">6500.00</RmtdAmt></RfrdDocAmt></Strd>
        </RmtInf>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09">
  <CstmrCdtTrfInitn>
{_hdr(f"NW-VND-{tag}{suffix}", 2, "26250.00", "Northwind Supply", "3333333333")}
    <PmtInf>
      <PmtInfId>VENDORS-{pay}</PmtInfId>
      <PmtMtd>TRF</PmtMtd>
      <PmtTpInf><CtgyPurp><Cd>SUPP</Cd></CtgyPurp></PmtTpInf>
      <ReqdExctnDt><Dt>{pay}</Dt></ReqdExctnDt>
      <Dbtr><Nm>Northwind Supply</Nm></Dbtr>
      <DbtrAcct><Id><Othr><Id>9000000003</Id></Othr></Id></DbtrAcct>
      <DbtrAgt><FinInstnId><ClrSysMmbId><MmbId>021000021</MmbId></ClrSysMmbId></FinInstnId></DbtrAgt>
      <CdtTrfTxInf>
        <PmtId><InstrId>VND-77</InstrId><EndToEndId>VND-77</EndToEndId></PmtId>
        <Amt><InstdAmt Ccy="USD">18500.00</InstdAmt></Amt>
        <CdtrAgt><FinInstnId><ClrSysMmbId><MmbId>026009593</MmbId></ClrSysMmbId></FinInstnId></CdtrAgt>
        <Cdtr><Nm>Pacific Components Inc</Nm><Id><OrgId><Othr><Id>PCI001</Id></Othr></OrgId></Id></Cdtr>
        <CdtrAcct><Id><Othr><Id>6600778899</Id></Othr></Id></CdtrAcct>{strd}
      </CdtTrfTxInf>
      <CdtTrfTxInf>
        <PmtId><InstrId>VND-78</InstrId><EndToEndId>VND-78</EndToEndId></PmtId>
        <Amt><InstdAmt Ccy="USD">7750.00</InstdAmt></Amt>
        <CdtrAgt><FinInstnId><BICFI>NWBKGB2LXXX</BICFI><Nm>NatWest</Nm><PstlAdr><Ctry>GB</Ctry></PstlAdr></FinInstnId></CdtrAgt>
        <Cdtr><Nm>Midlands Precision Ltd</Nm><Id><OrgId><Othr><Id>MPL01</Id></Othr></OrgId></Id>
          <PstlAdr><StrtNm>High St</StrtNm><BldgNb>10</BldgNb><PstCd>B11AA</PstCd><TwnNm>Birmingham</TwnNm><Ctry>GB</Ctry></PstlAdr></Cdtr>
        <CdtrAcct><Id><IBAN>GB29NWBK60161331926819</IBAN></Id></CdtrAcct>
        <RmtInf><Ustrd>Invoice MPL-5602</Ustrd></RmtInf>
      </CdtTrfTxInf>
    </PmtInf>
  </CstmrCdtTrfInitn>
</Document>
"""


def pain008_billing(suffix: str = "") -> str:
    today = utcnow().astimezone(ET).date()
    tag = utcnow().strftime("%Y%m%d%H%M%S")
    col = _next_weekday(today, 2).isoformat()

    def dd(e2e, amount, name, routing, acct, mandate=None, signed=None, ustrd="", tp="CACC"):
        m = (f"<DrctDbtTx><MndtRltdInf><MndtId>{mandate}</MndtId><DtOfSgntr>{signed}</DtOfSgntr></MndtRltdInf></DrctDbtTx>"
             if mandate else "")
        return f"""      <DrctDbtTxInf>
        <PmtId><EndToEndId>{e2e}</EndToEndId></PmtId>
        <InstdAmt Ccy="USD">{amount}</InstdAmt>
        {m}
        <DbtrAgt><FinInstnId><ClrSysMmbId><MmbId>{routing}</MmbId></ClrSysMmbId></FinInstnId></DbtrAgt>
        <Dbtr><Nm>{name}</Nm></Dbtr>
        <DbtrAcct><Id><Othr><Id>{acct}</Id></Othr></Id><Tp><Cd>{tp}</Cd></Tp></DbtrAcct>
        <RmtInf><Ustrd>{ustrd}</Ustrd></RmtInf>
      </DrctDbtTxInf>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.008.001.08">
  <CstmrDrctDbtInitn>
{_hdr(f"BW-DD-{tag}{suffix}", 3, "381.45", "Brightway Utilities", "2222222222")}
    <PmtInf>
      <PmtInfId>UTIL-{col}</PmtInfId>
      <PmtMtd>DD</PmtMtd>
      <NbOfTxs>3</NbOfTxs>
      <PmtTpInf><LclInstrm><Prtry>PPD</Prtry></LclInstrm><SeqTp>RCUR</SeqTp><CtgyPurp><Cd>UBIL</Cd></CtgyPurp></PmtTpInf>
      <ReqdColltnDt>{col}</ReqdColltnDt>
      <Cdtr><Nm>Brightway Utilities</Nm><Id><OrgId><Othr><Id>2222222222</Id></Othr></OrgId></Id></Cdtr>
      <CdtrAcct><Id><Othr><Id>9000000002</Id></Othr></Id></CdtrAcct>
      <CdtrAgt><FinInstnId><ClrSysMmbId><MmbId>021000021</MmbId></ClrSysMmbId></FinInstnId></CdtrAgt>
{dd("BILL-5001", "142.37", "James Chen", "021000021", "5550002222", "MND-JC-01", "2024-03-11", "Account 77-1001 October", "SVGS")}
{dd("BILL-5002", "151.98", "Olivia Martin", "011000015", "3300112233", "MND-OM-07", "2025-01-20", "Account 77-1002 October")}
{dd("BILL-5003", "87.10", "No Mandate Customer", "011000015", "3300999999", ustrd="Account 77-1003 October")}
    </PmtInf>
  </CstmrDrctDbtInitn>
</Document>
"""


SAMPLES = {
    "pain.001-payroll": ("Acme Payroll Services: 3 salary credits (one with an invalid routing number)", pain001_payroll),
    "pain.001-vendors": ("Northwind Supply: CTX vendor payment with structured remittance, plus a cross-border IAT credit", pain001_vendor),
    "pain.008-utility": ("Brightway Utilities: 3 recurring PPD debits (one without a mandate)", pain008_billing),
}
