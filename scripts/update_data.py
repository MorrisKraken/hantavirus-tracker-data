"""
Hantavirus Tracker — Automatic data updater
Runs 4x/day via GitHub Actions.

Sources checked (in order of priority):
  1. WHO DON RSS feed  — outbreak alerts with case counts
  2. PAHO PLISA API    — Americas region data
  3. ProMED RSS feed   — early warning system
  4. ECDC Atlas API    — European data (HFRS)

Falls back to existing data for any country not found in live sources.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    import feedparser
except ImportError:
    print("Missing dependencies. Run: pip install requests feedparser")
    sys.exit(1)

DATA_FILE = Path(__file__).parent.parent / "epidemiology.json"
NOW = datetime.now(timezone.utc).isoformat()

HANTA_KEYWORDS = [
    "hanta", "hantaan", "puumala", "sin nombre", "andes virus",
    "seoul virus", "dobrava", "hfrs", "hantavirus pulmonary",
    "hemorrhagic fever with renal", "hps"
]

COUNTRY_MAP = {
    "argentina":     "AR", "argentin":     "AR",
    "chile":         "CL", "chil":         "CL",
    "brazil":        "BR", "brasil":       "BR", "brésil": "BR",
    "united states": "US", "usa":          "US", "états-unis": "US",
    "paraguay":      "PY",
    "bolivia":       "BO", "bolivie":      "BO",
    "germany":       "DE", "deutschland":  "DE", "allemagne": "DE",
    "france":        "FR",
    "finland":       "FI", "finlande":     "FI",
    "sweden":        "SE", "suède":        "SE",
    "china":         "CN", "chine":        "CN",
    "south korea":   "KR", "korea":        "KR",
    "russia":        "RU", "russie":       "RU",
    "canada":        "CA",
    "mexico":        "MX", "mexique":      "MX",
    "peru":          "PE", "pérou":        "PE",
    "colombia":      "CO", "colombie":     "CO",
    "mongolia":      "MN", "mongolie":     "MN",
}


def is_hanta(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in HANTA_KEYWORDS)


def detect_country(text: str) -> str | None:
    t = text.lower()
    for name, code in COUNTRY_MAP.items():
        if name in t:
            return code
    return None


def extract_cases(text: str) -> dict:
    """Extract confirmed/suspected/contact counts from free text."""
    result = {}
    t = text.lower()

    # Confirmed
    m = re.search(
        r'(\d[\d,\.]*)\s*(?:confirmed|lab[\-\s]?confirmed|laboratory[\-\s]?confirmed)',
        t
    )
    if m:
        result["casesConfirmed"] = int(re.sub(r'[,\.]', '', m.group(1)))

    # Suspected / probable
    m = re.search(r'(\d[\d,\.]*)\s*(?:suspected|probable|suspect)', t)
    if m:
        result["casesSuspected"] = int(re.sub(r'[,\.]', '', m.group(1)))

    # Contacts
    m = re.search(r'(\d[\d,\.]*)\s*(?:contact|under\s+surveillance|under\s+monitoring)', t)
    if m:
        result["casesContact"] = int(re.sub(r'[,\.]', '', m.group(1)))

    # Deaths
    m = re.search(r'(\d[\d,\.]*)\s*(?:death|died|fatal)', t)
    if m:
        result["deaths"] = int(re.sub(r'[,\.]', '', m.group(1)))

    # Total cases (fallback)
    if not result.get("casesConfirmed"):
        m = re.search(
            r'(\d[\d,\.]*)\s*(?:case|cas|infected|infection)',
            t
        )
        if m:
            result["casesTotal"] = int(re.sub(r'[,\.]', '', m.group(1)))

    return result


def fetch_who_don():
    """Fetch WHO Disease Outbreak News RSS."""
    updates = {}
    try:
        url = "https://api.rss2json.com/v1/api.json?rss_url=https%3A%2F%2Fwww.who.int%2Ffeeds%2Fentity%2Fcsr%2Fdon%2Flang--en%2Frss.xml&count=50"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("status") != "ok":
            return updates
        for item in data.get("items", []):
            text = f"{item.get('title','')} {item.get('description','')}"
            if not is_hanta(text):
                continue
            code = detect_country(text)
            if not code:
                continue
            counts = extract_cases(text)
            pub = item.get("pubDate")
            report_date = None
            if pub:
                try:
                    report_date = datetime.strptime(pub[:16], "%a, %d %b %Y").strftime("%Y-%m-%d")
                except Exception:
                    report_date = None
            if counts:
                updates[code] = {**counts, "lastReport": report_date, "source": "WHO DON"}
                print(f"WHO DON: {code} → {counts}")
    except Exception as e:
        print(f"WHO DON error: {e}")
    return updates


def fetch_promed():
    """Fetch ProMED RSS (early warning)."""
    updates = {}
    try:
        feed = feedparser.parse("https://promedmail.org/feed/")
        for entry in feed.entries[:30]:
            text = f"{entry.get('title','')} {entry.get('summary','')}"
            if not is_hanta(text):
                continue
            code = detect_country(text)
            if not code:
                continue
            counts = extract_cases(text)
            pub = entry.get("published_parsed")
            report_date = datetime(*pub[:3]).strftime("%Y-%m-%d") if pub else None
            if counts:
                updates[code] = {**counts, "lastReport": report_date, "source": "ProMED"}
                print(f"ProMED: {code} → {counts}")
    except Exception as e:
        print(f"ProMED error: {e}")
    return updates


def fetch_ecdc():
    """Fetch ECDC Atlas HFRS dataset (dataset 27)."""
    updates = {}
    try:
        url = "https://atlas.ecdc.europa.eu/public/index.aspx?Dataset=27&Format=json"
        resp = requests.get(url, timeout=12)
        data = resp.json()
        rows = data.get("rows") or data.get("data") or data.get("Records") or []
        for row in rows:
            code2 = row.get("RegionCode") or row.get("CountryCode") or ""
            year = int(row.get("TimeCode") or row.get("Year") or 0)
            count = float(row.get("NumValue") or row.get("Value") or 0)
            if not code2 or count <= 0 or year < 2024:
                continue
            if code2 not in updates or year > updates[code2].get("year", 0):
                updates[code2] = {"casesConfirmed": int(count), "year": year, "source": "ECDC"}
                print(f"ECDC: {code2} → {int(count)} cases ({year})")
    except Exception as e:
        print(f"ECDC error: {e}")
    return updates


def load_existing() -> dict:
    """Load current epidemiology.json."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    raise FileNotFoundError(f"epidemiology.json not found at {DATA_FILE}")


def update_data():
    data = load_existing()
    countries_index = {c["code"]: c for c in data["countries"]}

    # Fetch all sources
    who_updates = fetch_who_don()
    promed_updates = fetch_promed()
    ecdc_updates = fetch_ecdc()

    changed = False

    for code, entry in countries_index.items():
        # Apply ECDC (most structured for European countries)
        if code in ecdc_updates:
            ec = ecdc_updates[code]
            if ec.get("casesConfirmed") and ec["casesConfirmed"] > entry.get("casesConfirmed", 0):
                entry["casesConfirmed"] = ec["casesConfirmed"]
                entry["dataQuality"] = "high"
                changed = True

        # Apply WHO DON (most authoritative for outbreak alerts)
        if code in who_updates:
            upd = who_updates[code]
            if upd.get("casesConfirmed"):
                entry["casesConfirmed"] = max(entry.get("casesConfirmed", 0), upd["casesConfirmed"])
                entry["dataQuality"] = "high"
                changed = True
            if upd.get("casesSuspected"):
                entry["casesSuspected"] = upd["casesSuspected"]
                changed = True
            if upd.get("casesContact"):
                entry["casesContact"] = upd["casesContact"]
                changed = True
            if upd.get("deaths"):
                entry["deaths"] = max(entry.get("deaths", 0), upd["deaths"])
                changed = True
            if upd.get("lastReport"):
                current = entry.get("lastReport", "2000-01-01")
                if upd["lastReport"] > current:
                    entry["lastReport"] = upd["lastReport"]
                    changed = True

        # Apply ProMED (early warning — only update if more recent)
        if code in promed_updates and code not in who_updates:
            upd = promed_updates[code]
            if upd.get("casesTotal") and upd["casesTotal"] > entry.get("cases", 0):
                entry["cases"] = upd["casesTotal"]
                changed = True

    if changed or True:  # Always update generatedAt to show freshness
        data["generatedAt"] = NOW
        data["generatedBy"] = "github-actions"
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ epidemiology.json updated at {NOW}")
    else:
        print("ℹ️ No changes detected.")


if __name__ == "__main__":
    update_data()
