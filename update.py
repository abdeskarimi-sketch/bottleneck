#!/usr/bin/env python3
"""
Bottleneck Monitor — daily updater.

Pulls price + fundamentals for every bottleneck name from Financial Modeling Prep,
computes the four-fingerprint signals (valuation-vs-history, momentum, crowding,
quality), and writes data.json. GitHub Actions runs this on a daily cron and commits
the refreshed data.json; GitHub Pages serves index.html which reads it.

The FMP API key is read from the FMP_API_KEY environment variable
(set as a GitHub repository Secret — never hardcode it).
"""

import os
import sys
import json
import time
import datetime as dt
from urllib import request, parse, error

API_KEY = os.environ.get("FMP_API_KEY", "").strip()
BASE = "https://financialmodelingprep.com/api/v3"
STABLE = "https://financialmodelingprep.com/stable"

# ---------------------------------------------------------------------------
# THE UNIVERSE — every bottleneck name, tagged by fingerprint thesis + factor.
# US/EU names quote cleanly on FMP. GCC/SG names (DFM/ADX/Tadawul/SGX) often do
# not — they are listed with quotable:false and shown as judgment-only cards.
# ---------------------------------------------------------------------------
UNIVERSE = [
    # --- AI-infrastructure stack (the conviction core) ---
    {"sym": "ASML",  "name": "ASML Holding",        "thesis": "Lithography monopoly — sole EUV maker",            "layer": "Semi · Equipment",  "factor": "AI",  "quotable": True},
    {"sym": "NVDA",  "name": "NVIDIA",              "thesis": "AI compute platform + CUDA lock-in",                "layer": "Semi · Compute",    "factor": "AI",  "quotable": True},
    {"sym": "TSM",   "name": "TSMC",               "thesis": "Leading-edge foundry monopoly",                     "layer": "Semi · Foundry",    "factor": "AI",  "quotable": True},
    {"sym": "ARM",   "name": "Arm Holdings",        "thesis": "Architecture IP / royalty annuity",                 "layer": "Semi · IP",         "factor": "AI",  "quotable": True},
    {"sym": "AMD",   "name": "Advanced Micro",      "thesis": "GPU/CPU challenger, AI accelerators",               "layer": "Semi · Compute",    "factor": "AI",  "quotable": True},
    {"sym": "AVGO",  "name": "Broadcom",            "thesis": "Custom silicon + networking moat",                  "layer": "Semi · Networking", "factor": "AI",  "quotable": True},
    {"sym": "AMAT",  "name": "Applied Materials",   "thesis": "Deposition/etch equipment oligopoly",               "layer": "Semi · Equipment",  "factor": "AI",  "quotable": True},
    {"sym": "LRCX",  "name": "Lam Research",        "thesis": "Etch & deposition duopoly",                         "layer": "Semi · Equipment",  "factor": "AI",  "quotable": True},
    {"sym": "KLAC",  "name": "KLA Corp",            "thesis": "Process control near-monopoly (the giant)",         "layer": "Semi · Metrology",  "factor": "AI",  "quotable": True},
    {"sym": "ONTO",  "name": "Onto Innovation",     "thesis": "Advanced-packaging inspection — the differentiated one", "layer": "Semi · Metrology", "factor": "AI", "quotable": True},
    {"sym": "CAMT",  "name": "Camtek",             "thesis": "HBM-packaging inspection (priced-for-perfection)",  "layer": "Semi · Metrology",  "factor": "AI",  "quotable": True},
    {"sym": "ALAB",  "name": "Astera Labs",         "thesis": "AI interconnect / connectivity silicon",            "layer": "Semi · Interconnect","factor": "AI", "quotable": True},
    {"sym": "CRDO",  "name": "Credo Technology",    "thesis": "AI interconnect (run +294%, insider-sold)",         "layer": "Semi · Interconnect","factor": "AI", "quotable": True},
    {"sym": "MU",    "name": "Micron",             "thesis": "Memory/HBM — CYCLICAL, watch the peak",             "layer": "Semi · Memory",     "factor": "AI",  "quotable": True},
    {"sym": "QCOM",  "name": "Qualcomm",           "thesis": "Licensing floor + data-center transition bet",      "layer": "Semi · IP",         "factor": "AI",  "quotable": True},
    {"sym": "FN",    "name": "Fabrinet",           "thesis": "Optical photonics manufacturing",                   "layer": "Semi · Photonics",  "factor": "AI",  "quotable": True},
    {"sym": "ORCL",  "name": "Oracle",             "thesis": "AI-cloud capacity / OCI",                           "layer": "Cloud",             "factor": "AI",  "quotable": True},
    {"sym": "CEG",   "name": "Constellation Energy","thesis": "Nuclear power for AI datacenters",                  "layer": "AI Power",          "factor": "AI",  "quotable": True},

    # --- Uncorrelated compounders (the balancers) ---
    {"sym": "LLY",   "name": "Eli Lilly",          "thesis": "Obesity/GLP-1 duopoly — widening moat",             "layer": "Healthcare",        "factor": "DIV", "quotable": True},
    {"sym": "NVO",   "name": "Novo Nordisk",       "thesis": "GLP-1 #2 — MOAT-DECAY FLAG (losing to LLY)",         "layer": "Healthcare",        "factor": "DIV", "quotable": True},
    {"sym": "UNH",   "name": "UnitedHealth",       "thesis": "Managed-care scale",                                "layer": "Healthcare",        "factor": "DIV", "quotable": True},
    {"sym": "BMY",   "name": "Bristol Myers",      "thesis": "Pharma income",                                     "layer": "Healthcare",        "factor": "DIV", "quotable": True},
    {"sym": "MP",    "name": "MP Materials",       "thesis": "Rare-earth bottleneck — only scaled US supply",     "layer": "Materials",         "factor": "DIV", "quotable": True},
    {"sym": "NU",    "name": "Nu Holdings",        "thesis": "LatAm fintech — most uncorrelated; review on credit","layer": "EM Fintech",        "factor": "DIV", "quotable": True},

    # --- Radar / spinoffs / defense ---
    {"sym": "HON",   "name": "Honeywell (pre-spin ref)","thesis": "Aerospace cert moat — track HONA post-spin",   "layer": "Aerospace",         "factor": "DIV", "quotable": True},
    {"sym": "FDXF",  "name": "FedEx Freight",      "thesis": "LTL network density bottleneck (spinoff)",          "layer": "Logistics",         "factor": "DIV", "quotable": True},
    {"sym": "AVAV",  "name": "AeroVironment",      "thesis": "Defense drones — backlog + litigation flag",        "layer": "Defense",           "factor": "DIV", "quotable": True},
    {"sym": "KTOS",  "name": "Kratos Defense",     "thesis": "Autonomous wingman / counter-UAS",                  "layer": "Defense",           "factor": "DIV", "quotable": True},
    {"sym": "RCAT",  "name": "Red Cat",            "thesis": "Army SRR drones — venture/momentum",                "layer": "Defense",           "factor": "SPEC","quotable": True},

    # --- GCC / Singapore monopolies (manual — FMP rarely quotes these) ---
    {"sym": "SALIK.AE",   "name": "Salik (DFM)",        "thesis": "Dubai toll monopoly — tax-free, USD-pegged", "layer": "GCC Infra",   "factor": "DIV", "quotable": False, "exchange": "DFM"},
    {"sym": "PARKIN.AE",  "name": "Parkin (DFM)",       "thesis": "Dubai parking monopoly",                     "layer": "GCC Infra",   "factor": "DIV", "quotable": False, "exchange": "DFM"},
    {"sym": "DEWA.AE",    "name": "DEWA (DFM)",         "thesis": "Water/power regulated monopoly",             "layer": "GCC Utility", "factor": "DIV", "quotable": False, "exchange": "DFM"},
    {"sym": "EMPOWER.AE", "name": "Empower (DFM)",      "thesis": "District cooling near-monopoly",             "layer": "GCC Utility", "factor": "DIV", "quotable": False, "exchange": "DFM"},
    {"sym": "ADNOCDRILL.AE","name": "ADNOC Drilling (ADX)","thesis": "90% contracted EBITDA — take-or-pay",     "layer": "GCC Energy",  "factor": "DIV", "quotable": False, "exchange": "ADX"},
    {"sym": "2082.SR",    "name": "ACWA Power (Tadawul)","thesis": "Desalination bottleneck — value-trap watch", "layer": "GCC Utility", "factor": "DIV", "quotable": False, "exchange": "Tadawul"},
    {"sym": "S68.SI",     "name": "SGX (Singapore)",    "thesis": "Exchange monopoly — financial infra toll",   "layer": "SG Infra",    "factor": "DIV", "quotable": False, "exchange": "SGX"},
]


def fetch(url, tries=3):
    """GET JSON with simple retry/backoff."""
    for i in range(tries):
        try:
            with request.urlopen(url, timeout=25) as r:
                return json.loads(r.read().decode())
        except error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 * (i + 1)); continue
            return None
        except Exception:
            time.sleep(1 * (i + 1))
    return None


def q(endpoint, **params):
    params["apikey"] = API_KEY
    return fetch(f"{BASE}/{endpoint}?{parse.urlencode(params)}")


def signal_from(metrics):
    """Turn raw metrics into the four fingerprint reads + an overall flag."""
    out = {}

    # Valuation vs its own history (PE percentile-ish): cheap if PE < 0.7*median-ish.
    pe = metrics.get("pe")
    out["valuation"] = None
    if isinstance(pe, (int, float)) and pe > 0:
        if pe < 20:   out["valuation"] = ("cheap", f"PE {pe:.0f}")
        elif pe < 40: out["valuation"] = ("fair",  f"PE {pe:.0f}")
        else:         out["valuation"] = ("rich",  f"PE {pe:.0f}")

    # Momentum: price vs 50/200-day.
    p, ma50, ma200 = metrics.get("price"), metrics.get("ma50"), metrics.get("ma200")
    out["momentum"] = None
    if p and ma50 and ma200:
        if p > ma50 > ma200:   out["momentum"] = ("strong", "above 50>200")
        elif p < ma50 < ma200: out["momentum"] = ("weak",   "below 50<200")
        else:                  out["momentum"] = ("mixed",  "crossing")

    # Crowding: 1y change — the higher it's run, the more crowded the read.
    chg = metrics.get("chg1y")
    out["crowding"] = None
    if isinstance(chg, (int, float)):
        if chg > 150:  out["crowding"] = ("hot",   f"+{chg:.0f}% 1y — discovered")
        elif chg > 40: out["crowding"] = ("warm",  f"+{chg:.0f}% 1y")
        elif chg < -20:out["crowding"] = ("cold",  f"{chg:.0f}% 1y — dislocated")
        else:          out["crowding"] = ("neutral",f"{chg:+.0f}% 1y")

    # Quality: net margin as a rough moat-economics proxy.
    nm = metrics.get("netMargin")
    out["quality"] = None
    if isinstance(nm, (int, float)):
        if nm > 0.25:  out["quality"] = ("elite", f"{nm*100:.0f}% net margin")
        elif nm > 0.08:out["quality"] = ("solid", f"{nm*100:.0f}% net margin")
        elif nm > 0:   out["quality"] = ("thin",  f"{nm*100:.0f}% net margin")
        else:          out["quality"] = ("loss",  "unprofitable")

    return out


def overall_flag(name, sig):
    """A single headline read combining the fingerprints — the 'so what'."""
    val = (sig.get("valuation") or (None,))[0]
    crowd = (sig.get("crowding") or (None,))[0]
    qual = (sig.get("quality") or (None,))[0]
    mom = (sig.get("momentum") or (None,))[0]

    if qual == "loss" and crowd == "hot":
        return ("CAUTION", "Run hard + unprofitable — momentum, not moat")
    if crowd == "cold" and qual in ("elite", "solid"):
        return ("OPPORTUNITY", "Quality repriced down — dislocation")
    if crowd == "hot" and val == "rich":
        return ("EXTENDED", "Discovered + richly priced — watch, don't chase")
    if val == "cheap" and qual in ("elite", "solid"):
        return ("VALUE", "Cheap relative to its economics")
    if mom == "strong" and qual in ("elite", "solid"):
        return ("COMPOUNDING", "Moat economics + momentum intact")
    return ("HOLD", "No strong signal — thesis-driven")


def build():
    if not API_KEY:
        print("ERROR: FMP_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    items = []
    quotable = [u for u in UNIVERSE if u["quotable"]]
    syms = ",".join(u["sym"] for u in quotable)

    # Batch quote (price, PE, 50/200 MA, 1y change all available on /quote)
    quotes = {q_["symbol"]: q_ for q_ in (q(f"quote/{syms}") or [])}

    for u in UNIVERSE:
        rec = {k: u[k] for k in ("sym", "name", "thesis", "layer", "factor", "quotable")}
        rec["exchange"] = u.get("exchange", "")
        if not u["quotable"]:
            rec["metrics"] = None
            rec["signals"] = None
            rec["flag"] = ("MANUAL", "GCC/SG — price not on feed; judgment card")
            items.append(rec); continue

        qd = quotes.get(u["sym"], {})
        metrics = {
            "price":  qd.get("price"),
            "pe":     qd.get("pe"),
            "ma50":   qd.get("priceAvg50"),
            "ma200":  qd.get("priceAvg200"),
            "chg1y":  None,  # filled below if available
            "netMargin": None,
            "mktCap": qd.get("marketCap"),
            "yearHigh": qd.get("yearHigh"),
            "yearLow":  qd.get("yearLow"),
        }
        # 1y change: derive from price vs (yearHigh+yearLow)/2 is crude; better: stock price change endpoint
        chg = q(f"stock-price-change/{u['sym']}")
        if chg and isinstance(chg, list) and chg:
            metrics["chg1y"] = chg[0].get("1Y")

        # net margin from TTM ratios (cheap single call)
        km = q(f"ratios-ttm/{u['sym']}")
        if km and isinstance(km, list) and km:
            metrics["netMargin"] = km[0].get("netProfitMarginTTM")

        sig = signal_from(metrics)
        rec["metrics"] = metrics
        rec["signals"] = sig
        rec["flag"] = overall_flag(u["name"], sig)
        items.append(rec)
        time.sleep(0.15)  # be kind to the rate limit

    payload = {
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "count": len(items),
        "items": items,
    }
    with open("data.json", "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote data.json — {len(items)} names at {payload['updated']}")


if __name__ == "__main__":
    build()
