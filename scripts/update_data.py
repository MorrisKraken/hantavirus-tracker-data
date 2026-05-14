#!/usr/bin/env python3
"""
Hantavirus Tracker — Data Update Script
========================================
Sources officielles uniquement :
  - WHO DON (Disease Outbreak News) — RSS
  - ProMED — RSS
  - ECDC — RSS

Ce script ne modifie JAMAIS les périodes courtes (d7/d30/today/h24/h72).
Il met à jour uniquement le champ `lastReport` quand un rapport officiel
plus récent est détecté.

GDELT a été supprimé définitivement : il comptait des articles de presse
et non des cas réels (ex : 250 articles → d7=250 "cas" = données fausses).
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
TIMEOUT = 12

# ── Mots-clés hantavirus ──────────────────────────────────────────────────────
HANTA_KEYWORDS = [
    "hanta", "hantaan", "puumala", "sin nombre", "andes virus",
    "seoul virus", "dobrava", "hfrs", "hantavirus pulmonary",
    "hemorrhagic fever with renal", "hps", "hantavirosis", "hantavirose",
    "hantavírus", "fièvre hémorragique à syndrome rénal",
]

# ── Mapping géographique → code ISO ──────────────────────────────────────────
COUNTRY_MAP = {
    "argentina":      "AR", "argentin":     "AR",
    "chile":          "CL", "chili":        "CL", "chilen":   "CL",
    "brazil":         "BR", "brasil":       "BR", "brésil":   "BR", "brazilian": "BR",
    "united states":  "US", "usa":          "US", "états-unis": "US", "american": "US",
    "paraguay":       "PY", "paraguayan":   "PY",
    "bolivia":        "BO", "bolivie":      "BO", "bolivian": "BO",
    "peru":           "PE", "pérou":        "PE", "peruvian": "PE",
    "colombia":       "CO", "colombie":     "CO",
    "germany":        "DE", "deutschland":  "DE", "allemagne": "DE", "german": "DE",
    "france":         "FR", "french":       "FR",
    "finland":        "FI", "finlande":     "FI", "finnish":  "FI",
    "sweden":         "SE", "suède":        "SE", "swedish":  "SE",
    "china":          "CN", "chine":        "CN", "chinese":  "CN",
    "south korea":    "KR", "korea":        "KR", "korean":   "KR",
    "russia":         "RU", "russie":       "RU", "russian":  "RU",
    "canada":         "CA", "canadian":     "CA",
    "mexico":         "MX", "mexique":      "MX", "mexican":  "MX",
    "mongolia":       "MN", "mongolie":     "MN",
    "netherlands":    "NL", "pays-bas":     "NL", "dutch":    "NL",
    "united kingdom": "GB", "uk":           "GB", "britain":  "GB",
    "switzerland":    "CH", "suisse":       "CH", "swiss":    "CH",
    "spain":          "ES", "espagne":      "ES", "spanish":  "ES",
    "austria":        "AT", "autriche":     "AT",
    "czech":          "CZ", "czechia":      "CZ", "tchèque":  "CZ",
    "venezuela":      "VE", "ecuador":      "EC", "uruguay":  "UY",
    "panama":         "PA",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_hanta(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in HANTA_KEYWORDS)


def detect_country(text: str) -> str | None:
    t = text.lower()
    # Les correspondances les plus longues d'abord pour éviter "us" → "australia"
    for name, code in sorted(COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
        if name in t:
            return code
    return None


# ── SOURCE 1 : WHO Disease Outbreak News ─────────────────────────────────────

def fetch_who_don() -> dict:
    """Retourne {code: date_str} pour les pays avec alertes WHO récentes."""
    reports = {}
    try:
        feed = feedparser.parse(
            "https://www.who.int/feeds/entity/csr/don/lang--en/rss.xml"
        )
        for entry in feed.entries[:50]:
            title   = getattr(entry, "title",   "") or ""
            summary = getattr(entry, "summary", "") or ""
            text = f"{title} {summary}"
            if not is_hanta(text):
                continue
            code = detect_country(text)
            if not code:
                continue
            pub = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
            if pub:
                date_str = datetime(*pub[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
            else:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if code not in reports or date_str > reports[code]:
                reports[code] = date_str
                print(f"  WHO DON : {code} → {date_str}")
    except Exception as e:
        print(f"  WHO DON erreur : {e}")
    return reports


# ── SOURCE 2 : ProMED ─────────────────────────────────────────────────────────

def fetch_promed() -> dict:
    """Retourne {code: date_str} pour les pays avec alertes ProMED récentes."""
    reports = {}
    try:
        feed = feedparser.parse("https://promedmail.org/feed/")
        for entry in feed.entries[:40]:
            title   = getattr(entry, "title",   "") or ""
            summary = getattr(entry, "summary", "") or ""
            text = f"{title} {summary}"
            if not is_hanta(text):
                continue
            code = detect_country(text)
            if not code:
                continue
            pub = getattr(entry, "published_parsed", None)
            if pub:
                date_str = datetime(*pub[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
            else:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if code not in reports or date_str > reports[code]:
                reports[code] = date_str
                print(f"  ProMED : {code} → {date_str}")
    except Exception as e:
        print(f"  ProMED erreur : {e}")
    return reports


# ── SOURCE 3 : ECDC ───────────────────────────────────────────────────────────

def fetch_ecdc() -> dict:
    """Retourne {code: date_str} si ECDC a publié récemment sur ce pays."""
    reports = {}
    try:
        feed = feedparser.parse("https://www.ecdc.europa.eu/en/rss.xml")
        for entry in feed.entries[:30]:
            title   = getattr(entry, "title",   "") or ""
            summary = getattr(entry, "summary", "") or ""
            text = f"{title} {summary}"
            if not is_hanta(text):
                continue
            code = detect_country(text)
            if not code:
                continue
            pub = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
            if pub:
                date_str = datetime(*pub[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
            else:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if code not in reports or date_str > reports[code]:
                reports[code] = date_str
                print(f"  ECDC : {code} → {date_str}")
    except Exception as e:
        print(f"  ECDC erreur : {e}")
    return reports


# ── Load / Save ───────────────────────────────────────────────────────────────

def load_data() -> dict:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict) -> None:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["lastUpdated"] = now_str
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n✅ epidemiology.json mis à jour ({now_str})")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 60)
    print(f"Hantavirus Tracker — Mise à jour des données")
    print(f"Date : {now_str}")
    print("=" * 60)
    print()
    print("IMPORTANT : ce script met à jour UNIQUEMENT lastReport.")
    print("Les champs cases/deaths/periods ne sont JAMAIS modifiés")
    print("automatiquement (données officielles mises à jour manuellement).")
    print()

    # 1. Charger les données actuelles
    print("[1] Chargement de epidemiology.json …")
    data = load_data()
    countries = data.get("countries", [])
    print(f"    {len(countries)} pays chargés")

    # 2. Récupérer les rapports officiels
    print("\n[2] Flux WHO DON …")
    who_reports = fetch_who_don()

    print("\n[3] Flux ProMED …")
    promed_reports = fetch_promed()

    print("\n[4] Flux ECDC …")
    ecdc_reports = fetch_ecdc()

    # Fusion : WHO > ECDC > ProMED (priorité décroissante)
    all_reports: dict[str, str] = {}
    for src in (promed_reports, ecdc_reports, who_reports):
        for code, date_str in src.items():
            if code not in all_reports or date_str > all_reports[code]:
                all_reports[code] = date_str

    print(f"\n    Total : {len(all_reports)} pays avec rapports détectés")

    # 3. Mettre à jour lastReport uniquement
    print("\n[5] Mise à jour des dates de rapport …")
    modified = False
    for country in countries:
        code = country.get("code")
        if not code or code not in all_reports:
            continue
        new_date = all_reports[code]
        old_date = country.get("lastReport") or ""
        if new_date > old_date:
            country["lastReport"] = new_date
            print(f"    ✓ {code} : {old_date!r} → {new_date!r}")
            modified = True

    # 4. Sauvegarder si modification
    if modified:
        print("\n[6] Sauvegarde …")
        save_data(data)
    else:
        print("\n    Aucune modification — données déjà à jour.")
        print("    (lastUpdated non modifié pour éviter un commit vide)")

    print("\n[Terminé]")


if __name__ == "__main__":
    main()
