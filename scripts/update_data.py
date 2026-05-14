"""
Hantavirus Tracker — Automatic data updater
Runs 4x/day via GitHub Actions.

Sources (par ordre de priorité) :
  1. WHO DON RSS     — alertes officielles OMS avec comptages textuels
  2. ProMED RSS      — système d'alerte précoce (ISID)
  3. ECDC Atlas      — données HFRS Europe (dataset 27)
  4. GDELT DOC API   — comptage d'articles par pays → mise à jour periods (d7, d30)
  5. Recalcul global — totalCases, totalDeaths, lethalityRate, periodData

Logique de mise à jour :
  - Les comptages cumulatifs (cases, deaths) ne diminuent jamais
  - Les periods (d7, d30) sont remplacées par les chiffres GDELT
  - La cohérence temporelle est vérifiée : d7 ≤ d30
"""

import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
    import feedparser
except ImportError:
    print("Missing dependencies. Run: pip install requests feedparser")
    sys.exit(1)

DATA_FILE = Path(__file__).parent.parent / "epidemiology.json"
NOW = datetime.now(timezone.utc).isoformat()
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
TIMEOUT = 12

HANTA_KEYWORDS = [
    "hanta", "hantaan", "puumala", "sin nombre", "andes virus",
    "seoul virus", "dobrava", "hfrs", "hantavirus pulmonary",
    "hemorrhagic fever with renal", "hps", "hantavirosis", "hantavirose",
    "hantavírus", "fièvre hémorragique à syndrome rénal",
]

# Mapping texte → code ISO
COUNTRY_MAP = {
    "argentina":        "AR", "argentin":         "AR",
    "chile":            "CL", "chili":            "CL", "chilen":       "CL",
    "brazil":           "BR", "brasil":           "BR", "brésil":       "BR", "brazilian": "BR",
    "united states":    "US", "usa":              "US", "états-unis":   "US", "american":  "US",
    "paraguay":         "PY", "paraguayan":       "PY",
    "bolivia":          "BO", "bolivie":          "BO", "bolivian":     "BO",
    "peru":             "PE", "pérou":            "PE", "peruvian":     "PE",
    "colombia":         "CO", "colombie":         "CO",
    "germany":          "DE", "deutschland":      "DE", "allemagne":    "DE", "german":    "DE",
    "france":           "FR", "french":           "FR",
    "finland":          "FI", "finlande":         "FI", "finnish":      "FI",
    "sweden":           "SE", "suède":            "SE", "swedish":      "SE",
    "china":            "CN", "chine":            "CN", "chinese":      "CN",
    "south korea":      "KR", "korea":            "KR", "korean":       "KR",
    "russia":           "RU", "russie":           "RU", "russian":      "RU",
    "canada":           "CA", "canadian":         "CA",
    "mexico":           "MX", "mexique":          "MX", "mexican":      "MX",
    "mongolia":         "MN", "mongolie":         "MN",
    "netherlands":      "NL", "pays-bas":         "NL", "dutch":        "NL",
    "united kingdom":   "GB", "uk":               "GB", "britain":      "GB",
    "switzerland":      "CH", "suisse":           "CH", "swiss":        "CH",
    "spain":            "ES", "espagne":          "ES", "spanish":      "ES",
}

# Pays suivis pour GDELT periods
GDELT_COUNTRIES = [
    "AR", "CL", "BR", "US", "PY", "BO", "PE", "CO", "MX",
    "DE", "FR", "FI", "SE", "RU", "NL", "GB", "CH", "AT", "CZ",
    "CN", "KR", "MN", "CA",
]


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def is_hanta(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in HANTA_KEYWORDS)


def detect_country(text: str) -> str | None:
    t = text.lower()
    # Longer matches first to avoid "us" matching "australia"
    for name, code in sorted(COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
        if name in t:
            return code
    return None


def extract_numbers(text: str) -> dict:
    """Extract case/death counts from free text via regex."""
    result = {}
    t = text.lower()

    patterns = [
        ("casesConfirmed", r'(\d[\d\s,\.]*)\s*(?:confirmed|lab[\-\s]?confirmed|laboratory[\-\s]?confirmed)\s*case'),
        ("casesSuspected", r'(\d[\d\s,\.]*)\s*(?:suspected|probable|suspect)\s*case'),
        ("casesContact",   r'(\d[\d\s,\.]*)\s*(?:contact|under\s+surveillance|under\s+monitoring)'),
        ("deaths",         r'(\d[\d\s,\.]*)\s*(?:death|died|fatal|fatality|fatalities)'),
        ("casesTotal",     r'(\d[\d\s,\.]*)\s*(?:total\s+)?(?:case|cas|infected|infection)'),
    ]
    for key, pat in patterns:
        m = re.search(pat, t)
        if m:
            val = re.sub(r'[\s,\.]', '', m.group(1))
            try:
                result[key] = int(val)
            except ValueError:
                pass

    return result


def parse_date(raw: str) -> str | None:
    """Parse various date formats to YYYY-MM-DD."""
    if not raw:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:len(fmt)+4], fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    try:
        return raw[:10]
    except Exception:
        return None


# ─── SOURCE 1 : WHO Disease Outbreak News ────────────────────────────────────

def fetch_who_don() -> dict:
    updates = {}
    try:
        url = (
            "https://api.rss2json.com/v1/api.json"
            "?rss_url=https%3A%2F%2Fwww.who.int%2Ffeeds%2Fentity%2Fcsr%2Fdon%2Flang--en%2Frss.xml"
            "&count=50"
        )
        resp = requests.get(url, timeout=TIMEOUT)
        data = resp.json()
        if data.get("status") != "ok":
            return updates
        for item in data.get("items", []):
            title = item.get("title", "")
            body = item.get("description", "") or item.get("content", "")
            text = f"{title} {body}"
            if not is_hanta(text):
                continue
            code = detect_country(text)
            if not code:
                continue
            counts = extract_numbers(text)
            report_date = parse_date(item.get("pubDate", ""))
            if counts:
                updates[code] = {**counts, "lastReport": report_date, "source": "WHO DON"}
                print(f"  WHO DON: {code} → {counts}")
    except Exception as e:
        print(f"  WHO DON error: {e}")
    return updates


# ─── SOURCE 2 : ProMED ────────────────────────────────────────────────────────

def fetch_promed() -> dict:
    updates = {}
    try:
        feed = feedparser.parse("https://promedmail.org/feed/")
        for entry in feed.entries[:40]:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            text = f"{title} {summary}"
            if not is_hanta(text):
                continue
            code = detect_country(text)
            if not code:
                continue
            counts = extract_numbers(text)
            pub = entry.get("published_parsed")
            report_date = datetime(*pub[:3]).strftime("%Y-%m-%d") if pub else None
            if counts:
                updates[code] = {**counts, "lastReport": report_date, "source": "ProMED"}
                print(f"  ProMED: {code} → {counts}")
    except Exception as e:
        print(f"  ProMED error: {e}")
    return updates


# ─── SOURCE 3 : ECDC Atlas (HFRS = dataset 27) ───────────────────────────────

def fetch_ecdc() -> dict:
    updates = {}
    try:
        # ECDC Surveillance Atlas public API
        url = "https://atlas.ecdc.europa.eu/public/index.aspx?Dataset=27&Format=json"
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            print(f"  ECDC HTTP {resp.status_code}")
            return updates
        data = resp.json()
        rows = (
            data.get("rows") or data.get("data") or
            data.get("Records") or data.get("records") or []
        )
        for row in rows:
            code = (row.get("RegionCode") or row.get("CountryCode") or "").upper()
            year = int(row.get("TimeCode") or row.get("Year") or 0)
            try:
                count = int(float(row.get("NumValue") or row.get("Value") or 0))
            except (ValueError, TypeError):
                continue
            if not code or count <= 0 or year < 2020:
                continue
            # Keep the most recent year
            if code not in updates or year > updates[code].get("year", 0):
                updates[code] = {"casesConfirmed": count, "year": year, "source": "ECDC"}
                print(f"  ECDC: {code} → {count} cas ({year})")
    except Exception as e:
        print(f"  ECDC error: {e}")
    return updates


# ─── SOURCE 4 : GDELT — comptage articles par pays ───────────────────────────

def count_gdelt(country_code: str, days: int) -> int:
    """Count hantavirus GDELT articles for a country over the last N days."""
    try:
        url = (
            f"https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query=hantavirus&sourcecountry={country_code}"
            f"&mode=artlist&maxrecords=250&format=json&timespan={days}d"
        )
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code != 200:
            return 0
        data = resp.json()
        return len(data.get("articles", []))
    except Exception:
        return 0


def fetch_gdelt_periods() -> dict:
    """
    Returns {code: {d7: N, d30: N}} for all tracked countries.
    Uses article count as activity proxy for the periods field.
    Two API calls per country — batched with short pauses.
    """
    result = {}
    for code in GDELT_COUNTRIES:
        d7  = count_gdelt(code, 7)
        time.sleep(0.3)
        d30 = count_gdelt(code, 30)
        time.sleep(0.3)
        # Enforce monotonicity: d7 ≤ d30
        if d7 > d30:
            d7 = d30
        result[code] = {"d7": d7, "d30": d30}
        print(f"  GDELT {code}: d7={d7}  d30={d30}")
    return result


# ─── LOAD / SAVE ─────────────────────────────────────────────────────────────

def load() -> dict:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save(data: dict) -> None:
    data["generatedAt"] = NOW
    data["generatedBy"] = "github-actions"
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ epidemiology.json mis à jour ({NOW})")


# ─── RECALCUL STATS GLOBALES ─────────────────────────────────────────────────

def recompute_global(data: dict) -> None:
    countries = [c for c in data["countries"] if c["code"] != "MARITIME"]
    total_cases  = sum(c.get("cases", 0) for c in countries)
    total_deaths = sum(c.get("deaths", 0) for c in countries)
    lethality    = round(total_deaths / total_cases * 100, 1) if total_cases > 0 else 0
    active       = sum(1 for c in countries if c.get("alertLevel") in ("high", "critical"))
    all_d7       = sum(c.get("periods", {}).get("d7",  0) for c in countries)
    all_d30      = sum(c.get("periods", {}).get("d30", 0) for c in countries)

    gs = data["globalStats"]
    gs["totalCases"]           = total_cases
    gs["totalDeaths"]          = total_deaths
    gs["lethalityRate"]        = lethality
    gs["activeOutbreaks"]      = active
    gs["periodData"]["d7"]["cases"]  = all_d7
    gs["periodData"]["d30"]["cases"] = all_d30

    print(f"\n  Global: {total_cases} cas / {total_deaths} décès / létalité {lethality}%")
    print(f"  Périodes: d7={all_d7}  d30={all_d30}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print("═" * 60)
    print(f"🚀  Mise à jour épidémiologique — {NOW}")
    print("═" * 60)

    data = load()
    index = {c["code"]: c for c in data["countries"]}
    changed = False

    # ── 1. WHO DON ──────────────────────────────────────────────
    print("\n📡 WHO Disease Outbreak News…")
    who = fetch_who_don()

    # ── 2. ProMED ───────────────────────────────────────────────
    print("\n📡 ProMED RSS…")
    promed = fetch_promed()

    # ── 3. ECDC ─────────────────────────────────────────────────
    print("\n📡 ECDC Atlas HFRS…")
    ecdc = fetch_ecdc()

    # ── 4. GDELT periods ────────────────────────────────────────
    print("\n📡 GDELT — comptage articles par pays…")
    gdelt_periods = fetch_gdelt_periods()

    # ── Application des mises à jour ───────────────────────────
    print("\n✏️  Application…")
    for code, entry in index.items():

        # ECDC — données confirmées pour l'Europe
        if code in ecdc:
            ec = ecdc[code]
            new_confirmed = ec.get("casesConfirmed", 0)
            if new_confirmed > entry.get("casesConfirmed", 0):
                entry["casesConfirmed"] = new_confirmed
                # Mettre à jour cases (total) si supérieur
                if new_confirmed > entry.get("cases", 0):
                    entry["cases"] = new_confirmed
                entry["dataQuality"] = "high"
                entry["lastReport"] = TODAY
                changed = True

        # WHO DON — alertes officielles
        if code in who:
            upd = who[code]
            for field in ("casesConfirmed", "casesSuspected", "casesContact"):
                if upd.get(field) and upd[field] > entry.get(field, 0):
                    entry[field] = upd[field]
                    changed = True
            if upd.get("deaths") and upd["deaths"] > entry.get("deaths", 0):
                entry["deaths"] = upd["deaths"]
                changed = True
            if upd.get("casesTotal") and upd["casesTotal"] > entry.get("cases", 0):
                entry["cases"] = upd["casesTotal"]
                changed = True
            if upd.get("lastReport") and upd["lastReport"] > entry.get("lastReport", "2000-01-01"):
                entry["lastReport"] = upd["lastReport"]
                changed = True
            entry["dataQuality"] = "high"

        # ProMED — alerte précoce (seulement si pas de WHO pour ce pays)
        if code in promed and code not in who:
            upd = promed[code]
            if upd.get("casesTotal") and upd["casesTotal"] > entry.get("cases", 0):
                entry["cases"] = upd["casesTotal"]
                changed = True
            if upd.get("lastReport") and upd["lastReport"] > entry.get("lastReport", "2000-01-01"):
                entry["lastReport"] = upd["lastReport"]
                changed = True

        # GDELT periods — toujours mis à jour
        if code in gdelt_periods:
            gp = gdelt_periods[code]
            periods = entry.get("periods", {})
            if gp["d7"] != periods.get("d7") or gp["d30"] != periods.get("d30"):
                periods["d7"]  = gp["d7"]
                periods["d30"] = gp["d30"]
                entry["periods"] = periods
                changed = True

    # ── Recalcul stats globales ─────────────────────────────────
    print("\n📊 Recalcul stats globales…")
    recompute_global(data)

    # ── Sauvegarde ──────────────────────────────────────────────
    save(data)

    if not changed:
        print("  ℹ️  Aucune modification des données sources détectée.")


if __name__ == "__main__":
    main()
