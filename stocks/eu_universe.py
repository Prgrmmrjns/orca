"""Liquid European equity universe for bigmove_eu_v1 (paper only).

Curated large/mid caps across DAX 40, MDAX, Euro Stoxx 50 and large STOXX 600
names on free yfinance suffixes (.DE/.PA/.AS/.MI/.MC/.SW/.L). Filters applied
at runtime: min price EUR 5, min 20d ADV EUR 10M, exclude ETFs.
"""
from __future__ import annotations

# Static seed list — liquid names; runtime ADV/price filters shrink further.
EU_UNIVERSE_SEED: list[str] = [
    # DAX 40 / Germany
    "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "AIR.DE", "MBG.DE", "BMW.DE", "BAS.DE",
    "DHL.DE", "DB1.DE", "MUV2.DE", "IFX.DE", "ADS.DE", "BEI.DE", "DBK.DE", "EOAN.DE",
    "RWE.DE", "VOW3.DE", "VNA.DE", "HEN3.DE", "MRK.DE", "SHL.DE", "DTG.DE", "ENR.DE",
    "BAYN.DE", "CON.DE", "HNR1.DE", "MTX.DE", "PAH3.DE", "P911.DE", "QIA.DE", "RHM.DE",
    "SRT3.DE", "SY1.DE", "ZAL.DE", "CBK.DE", "BNR.DE", "FRE.DE", "G1A.DE", "HEI.DE",
    # MDAX / liquid mid
    "AIXA.DE", "AFX.DE", "KGX.DE", "LEG.DE", "NEM.DE", "RAA.DE", "SDF.DE", "TLX.DE",
    "WCH.DE", "1COV.DE", "FME.DE", "HOT.DE", "KBX.DE", "LHA.DE", "NDA.DE", "SHF.DE",
    # France CAC / Euro Stoxx
    "MC.PA", "OR.PA", "AIR.PA", "SU.PA", "SAN.PA", "AI.PA", "TTE.PA", "BNP.PA",
    "CS.PA", "EL.PA", "DG.PA", "SAF.PA", "RI.PA", "BN.PA", "CAP.PA", "HO.PA",
    "KER.PA", "ORA.PA", "SGO.PA", "VIE.PA", "ENGI.PA", "ML.PA", "PUB.PA", "STMPA.PA",
    "ACA.PA", "GLE.PA", "CA.PA", "RNO.PA", "URW.PA", "DSY.PA",
    # Netherlands
    "ASML.AS", "ADYEN.AS", "INGA.AS", "PHIA.AS", "AD.AS", "HEIA.AS", "WKL.AS",
    "PRX.AS", "ABN.AS", "DSFIR.AS", "NN.AS", "KPN.AS",
    # Italy
    "ENEL.MI", "ENI.MI", "ISP.MI", "UCG.MI", "G.MI", "RACE.MI", "CPR.MI",
    "TIT.MI", "TRN.MI", "MB.MI", "AMP.MI",
    # Spain
    "SAN.MC", "BBVA.MC", "ITX.MC", "TEF.MC", "IBE.MC", "REP.MC", "AMS.MC", "FER.MC",
    "AENA.MC", "CLNX.MC", "ELE.MC", "GRF.MC",
    # Switzerland
    "NESN.SW", "RO.SW", "NOVN.SW", "UBSG.SW", "ABBN.SW", "ZURN.SW", "CFR.SW",
    "SREN.SW", "GIVN.SW", "SIKA.SW", "LONN.SW", "GEBN.SW", "PGHN.SW", "ALC.SW",
    # UK large STOXX-ish
    "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L", "DGE.L", "BATS.L",
    "RIO.L", "GLEN.L", "NG.L", "LLOY.L", "VOD.L", "BARC.L", "REL.L", "CPG.L",
    "AAL.L", "PRU.L", "EXPN.L", "BA.L", "IMB.L", "TSCO.L", "RR.L", "SSE.L",
]

# Map short / local names → yfinance tickers for news matching
EU_NAME_HINTS: dict[str, str] = {
    "SAP": "SAP.DE",
    "SIEMENS": "SIE.DE",
    "ALLIANZ": "ALV.DE",
    "DEUTSCHE TELEKOM": "DTE.DE",
    "TELEKOM": "DTE.DE",
    "AIRBUS": "AIR.PA",
    "MERCEDES": "MBG.DE",
    "BMW": "BMW.DE",
    "BASF": "BAS.DE",
    "DEUTSCHE BANK": "DBK.DE",
    "DEUTSCHE BÖRSE": "DB1.DE",
    "DEUTSCHE BOERSE": "DB1.DE",
    "INFINEON": "IFX.DE",
    "ADIDAS": "ADS.DE",
    "BAYER": "BAYN.DE",
    "VOLKSWAGEN": "VOW3.DE",
    "RWE": "RWE.DE",
    "E.ON": "EOAN.DE",
    "EON": "EOAN.DE",
    "MUENCHENER RUECK": "MUV2.DE",
    "MUNICH RE": "MUV2.DE",
    "HENKEL": "HEN3.DE",
    "MERCK": "MRK.DE",
    "ZALANDO": "ZAL.DE",
    "COMMERZBANK": "CBK.DE",
    "LVMH": "MC.PA",
    "LOREAL": "OR.PA",
    "L'OREAL": "OR.PA",
    "TOTALENERGIES": "TTE.PA",
    "TOTAL": "TTE.PA",
    "SANOFI": "SAN.PA",
    "SCHNEIDER": "SU.PA",
    "AIR LIQUIDE": "AI.PA",
    "BNP": "BNP.PA",
    "AXA": "CS.PA",
    "ESSILOR": "EL.PA",
    "VINC I": "DG.PA",
    "VINCI": "DG.PA",
    "SAFRAN": "SAF.PA",
    "PERNOD": "RI.PA",
    "DANONE": "BN.PA",
    "CAPGEMINI": "CAP.PA",
    "THALES": "HO.PA",
    "KERING": "KER.PA",
    "ORANGE": "ORA.PA",
    "SAINT-GOBAIN": "SGO.PA",
    "ENGIE": "ENGI.PA",
    "MICHELIN": "ML.PA",
    "PUBLICIS": "PUB.PA",
    "STMICRO": "STM.PA",
    "RENAULT": "RNO.PA",
    "ASML": "ASML.AS",
    "ADYEN": "ADYEN.AS",
    "ING": "INGA.AS",
    "PHILIPS": "PHIA.AS",
    "AHOLD": "AD.AS",
    "HEINEKEN": "HEIA.AS",
    "WOLTERS": "WKL.AS",
    "PROSUS": "PRX.AS",
    "ENEL": "ENEL.MI",
    "ENI": "ENI.MI",
    "INTESA": "ISP.MI",
    "UNICREDIT": "UCG.MI",
    "FERRARI": "RACE.MI",
    "SANTANDER": "SAN.MC",
    "BBVA": "BBVA.MC",
    "INDITEX": "ITX.MC",
    "TELEFONICA": "TEF.MC",
    "IBERDROLA": "IBE.MC",
    "REPSOL": "REP.MC",
    "AMADEUS": "AMS.MC",
    "NESTLE": "NESN.SW",
    "NESTLÉ": "NESN.SW",
    "ROCHE": "RO.SW",
    "NOVARTIS": "NOVN.SW",
    "STMICROELECTRONICS": "STMPA.PA",
    "UBS": "UBSG.SW",
    "ABB": "ABBN.SW",
    "ZURICH": "ZURN.SW",
    "RICHEMONT": "CFR.SW",
    "SWISS RE": "SREN.SW",
    "GIVAUDAN": "GIVN.SW",
    "SIKA": "SIKA.SW",
    "LONZA": "LONN.SW",
    "SHELL": "SHEL.L",
    "ASTRAZENECA": "AZN.L",
    "HSBC": "HSBA.L",
    "UNILEVER": "ULVR.L",
    "BP": "BP.L",
    "GSK": "GSK.L",
    "DIAGEO": "DGE.L",
    "BAT": "BATS.L",
    "RIO TINTO": "RIO.L",
    "GLENCORE": "GLEN.L",
    "NATIONAL GRID": "NG.L",
    "LLOYDS": "LLOY.L",
    "VODAFONE": "VOD.L",
    "BARCLAYS": "BARC.L",
    "RELX": "REL.L",
    "COMPASS": "CPG.L",
    "ANGLO AMERICAN": "AAL.L",
    "PRUDENTIAL": "PRU.L",
    "EXPERIAN": "EXPN.L",
    "BAE": "BA.L",
    "IMPERIAL BRANDS": "IMB.L",
    "TESCO": "TSCO.L",
    "ROLLS-ROYCE": "RR.L",
    "SSE": "SSE.L",
    "WIENERBERGER": "WIE.VI",
}

EU_SUFFIXES = (".DE", ".PA", ".AS", ".MI", ".MC", ".SW", ".L", ".VI", ".BR", ".HE")


def normalize_eu_ticker(raw: str) -> str | None:
    t = (raw or "").strip().upper().replace(" ", "")
    if not t:
        return None
    if any(t.endswith(s) for s in EU_SUFFIXES):
        return t
    # bare symbol → try seed match
    for seed in EU_UNIVERSE_SEED:
        if seed.split(".")[0] == t:
            return seed
    return None


def match_company_to_ticker(text: str) -> list[str]:
    """Best-effort company-name → ticker from headlines."""
    blob = (text or "").upper()
    hits: list[str] = []
    seen: set[str] = set()
    for name, ticker in sorted(EU_NAME_HINTS.items(), key=lambda kv: -len(kv[0])):
        if name in blob and ticker not in seen:
            hits.append(ticker)
            seen.add(ticker)
    # Explicit yfinance-style symbols in text
    import re

    for m in re.finditer(r"\b([A-Z0-9]{1,5}\.(?:DE|PA|AS|MI|MC|SW|L|VI|BR|HE))\b", blob):
        t = m.group(1)
        if t not in seen:
            hits.append(t)
            seen.add(t)
    return hits
