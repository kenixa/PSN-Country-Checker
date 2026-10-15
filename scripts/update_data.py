#!/usr/bin/env python3
"""Free scheduled PSN deal scanner for GitHub Pages.

Only public, unauthenticated Eneba GET pages are fetched. No checkout/session
cookies are forwarded through public proxy servers.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DATA_FILE = DOCS / "data.json"
REGIONS = json.loads((ROOT / "scripts" / "regions.json").read_text(encoding="utf-8"))
REGION_BY_CODE = {r["country"]: r for r in REGIONS}
PRIORITY = ["AU", "NL", "DE"]  # always refreshed: Australia, Netherlands, Germany
COUNTRY_BATCH_SIZE = 10
MAX_PROXY_CANDIDATES = 48
MAX_VALID_PROXIES = 3
MAX_REGION_WORKERS = 5
MAX_PROXY_WORKERS = 20
DEAL_MAX_AGE_SECONDS = 60 * 60
GERMAN_PSN_CATEGORY_URL = "https://www.eneba.com/store/psn-gift-cards/germany"
# The detail page includes Eneba's denomination picker for German PSN cards.
GERMAN_PSN_DETAIL_URL = "https://www.eneba.com/psn-playstation-network-card-60-eur-de-psn-key-germany"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
CURRENCY_CODES = sorted(set(r["currency"] for r in REGIONS) | {"EUR", "USD", "GBP", "AUD", "CAD", "NZD", "JPY", "SGD", "HKD", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "BRL", "ZAR", "INR", "MXN", "TRY", "THB", "IDR", "KRW", "TWD", "MYR", "ILS", "RON", "SAR", "AED", "QAR", "KWD", "BHD", "OMR", "PEN", "COP", "ARS", "CLP"})
CURRENCY_ALT = "|".join(sorted(CURRENCY_CODES, key=len, reverse=True))
NUMBER = r"(?:\d{1,3}(?:[\s.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})


def utc_iso(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms else None


def log(msg: str) -> None:
    print(f"[PSN Top20] {msg}", flush=True)


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def get_fx_rates() -> dict[str, float]:
    for url in (
        "https://api.frankfurter.dev/v1/latest?base=EUR",
        "https://open.er-api.com/v6/latest/EUR",
    ):
        try:
            response = SESSION.get(url, timeout=(4, 8))
            response.raise_for_status()
            data = response.json()
            rates = data.get("rates") or {}
            if len(rates) > 10:
                rates["EUR"] = 1.0
                log(f"Wechselkurse geladen: {len(rates)} Währungen")
                return rates
        except Exception as exc:
            log(f"Wechselkursquelle nicht verfügbar ({urlparse(url).netloc}): {exc}")
    return {"EUR": 1.0}


def to_eur(amount: float, currency: str, rates: dict[str, float]) -> float | None:
    currency = currency.upper()
    if currency == "EUR":
        return amount
    rate = rates.get(currency)
    if isinstance(rate, (int, float)) and rate > 0:
        return amount / rate
    return None


def parse_number(raw: str) -> float | None:
    s = re.sub(r"[\s\u00a0]", "", str(raw or ""))
    if not s:
        return None
    comma, dot = s.rfind(","), s.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal = "," if comma > dot else "."
        s = s.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif comma >= 0:
        digits = len(s) - comma - 1
        s = s.replace(",", ".") if digits in (1, 2) else s.replace(",", "")
    elif dot >= 0 and len(s) - dot - 1 == 3 and re.fullmatch(r"\d{1,3}\.\d{3}", s):
        s = s.replace(".", "")
    try:
        n = float(s)
        return n if n > 0 else None
    except ValueError:
        return None


def is_public_ipv4(ip: str) -> bool:
    m = re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})", ip.strip())
    if not m:
        return False
    parts = [int(x) for x in m.groups()]
    if any(n > 255 for n in parts):
        return False
    a, b, c, d = parts
    if a in (0, 10, 127) or a >= 224 or (a == 169 and b == 254):
        return False
    if a == 172 and 16 <= b <= 31:
        return False
    if a == 192 and b == 168:
        return False
    if a == 100 and 64 <= b <= 127:
        return False
    if a == 192 and b == 0 and c in (0, 2):
        return False
    if a == 198 and b in (18, 19, 51):
        return False
    if a == 203 and b == 0 and c == 113:
        return False
    return True


def get_proxy_candidates(country: str) -> list[str]:
    """Collect a diverse candidate pool from several free country-filtered lists."""
    cc = country.lower()
    urls = [
        f"https://raw.githubusercontent.com/ProxyScrape/free-proxy-list/main/proxies/countries/{cc}/http/data.txt",
        f"https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/by-country/{country}.txt",
        f"https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/by-country/{cc}/http.txt",
        # Mixed-protocol list; the live HTTPS exit-IP check filters unsupported entries.
        f"https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/countries/{country}/data.txt",
    ]
    found: list[str] = []
    seen: set[str] = set()
    per_source_limit = max(8, MAX_PROXY_CANDIDATES // len(urls))
    for url in urls:
        source_count = 0
        try:
            response = SESSION.get(url, timeout=(4, 8))
            if not response.ok:
                continue
            for line in response.text.splitlines():
                m = re.search(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3}):(\d{1,5})(?!\d)", line.strip())
                if not m:
                    continue
                ip, port = m.group(1), int(m.group(2))
                proxy = f"{ip}:{port}"
                if not is_public_ipv4(ip) or not (1 <= port <= 65535) or proxy in seen:
                    continue
                seen.add(proxy)
                found.append(proxy)
                source_count += 1
                if source_count >= per_source_limit:
                    break
        except Exception as exc:
            log(f"Proxyquelle nicht verfügbar für {country}: {urlparse(url).netloc} ({exc})")
    random.shuffle(found)
    candidates = found[:MAX_PROXY_CANDIDATES]
    log(f"{country}: {len(candidates)} Proxy-Kandidaten aus {len(urls)} Quellen")
    return candidates


def _verify_one_proxy(item: tuple[str, str]) -> tuple[str, str]:
    """Confirm the actual exit country, falling back to a second Geo-IP endpoint."""
    proxy, expected_country = item
    proxy_url = f"http://{proxy}"
    proxy_cfg = {"http": proxy_url, "https": proxy_url}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    checks = (
        ("https://ipwho.is/", "country_code"),
        ("https://ipapi.co/json/", "country_code"),
    )
    for url, field in checks:
        try:
            response = requests.get(url, proxies=proxy_cfg, headers=headers, timeout=(2.5, 4))
            if not response.ok:
                continue
            data = response.json()
            got = str(data.get(field) or "").upper()
            if got:
                return proxy, got if got == expected_country else ""
        except Exception:
            continue
    return proxy, ""


def verify_country_proxies(country: str, candidates: list[str]) -> list[str]:
    """Verify real exit country through the candidate itself, then use it only for public GETs."""
    matches: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=min(MAX_PROXY_WORKERS, max(1, len(candidates)))) as pool:
        futures = [pool.submit(_verify_one_proxy, (proxy, country)) for proxy in candidates]
        for future in cf.as_completed(futures):
            try:
                proxy, found_country = future.result()
                if found_country == country:
                    matches.append(proxy)
                    if len(matches) >= MAX_VALID_PROXIES:
                        for f in futures:
                            f.cancel()
                        break
            except Exception:
                pass
    return matches


def title_and_nominal(anchor) -> tuple[str, float, str] | None:
    # Eneba's denomination picker stores its full title in aria-label while visible
    # text often only contains "60 EUR · €47.71 · 1.26 EUR per €1".
    img = anchor.find("img", alt=True)
    title = ""
    if img:
        title = str(img.get("alt") or "").strip()
    title = (
        title
        or str(anchor.get("title") or "").strip()
        or str(anchor.get("aria-label") or "").strip()
        or str(anchor.get("data-testid") or "").strip()
        or anchor.get_text(" ", strip=True)
    )
    pat = re.compile(r"PlayStation\s+Network\s+Card\s+(" + NUMBER + r")\s+([A-Z]{3})\b", re.I)
    match = pat.search(title)
    if not match:
        return None
    amount = parse_number(match.group(1))
    currency = match.group(2).upper()
    if not amount or currency not in CURRENCY_CODES:
        return None
    return title, amount, currency


def is_german_psn_card(title: str, nominal_currency: str) -> bool:
    """Only keep German PSN denominations, regardless of the proxy's country."""
    if nominal_currency.upper() != "EUR":
        return False
    normalized = str(title or "")
    return bool(
        re.search(r"\(\s*DE\s*\)", normalized, re.I)
        or re.search(r"\bGERMANY\b", normalized, re.I)
    )


def price_from_card_text(
    text: str,
    fallback_currency: str,
    nominal_amount: float | None = None,
    nominal_currency: str | None = None,
) -> tuple[float, str] | None:
    """Extract seller price without mistaking '5% cashback' or face value for price."""
    text = re.sub(r"\s+", " ", text).strip()
    fallback_currency = str(fallback_currency or "EUR").upper()
    nominal_currency = str(nominal_currency or "").upper()

    # 1) An explicit "from/ab" prefix identifies the seller price, including PLN/SEK/etc.
    explicit = re.compile(
        r"\b(?:from|ab|à\s+partir\s+de)\s*"
        r"(?:(?P<before>" + CURRENCY_ALT + r")\s*)?"
        r"(?P<symbol>[€£$])?\s*"
        r"(?P<amount>" + NUMBER + r")\s*"
        r"(?P<after>" + CURRENCY_ALT + r")?\b",
        re.I,
    )
    m = explicit.search(text)
    if m:
        code = (m.group("before") or m.group("after") or "").upper()
        symbol = m.group("symbol")
        currency = code or ("EUR" if symbol == "€" else "GBP" if symbol == "£" else fallback_currency)
        amount = parse_number(m.group("amount"))
        if amount and currency in CURRENCY_CODES:
            # A symbol/code can appear in incidental text, so never accept nominal face value here
            # unless the explicit "from" price is truly the same number (rare but valid).
            return amount, currency

    # 2) Visible currency symbols mark the actual price more reliably than "EUR per €1".
    symbol_pattern = re.compile(r"([€£$])\s*(" + NUMBER + r")")
    for m in symbol_pattern.finditer(text):
        # The reference amount in "EUR per €1" is not the selling price.
        prefix = text[max(0, m.start() - 12):m.start()].lower()
        if re.search(r"\b(?:per|pro)\s*$", prefix):
            continue
        symbol = m.group(1)
        currency = "EUR" if symbol == "€" else "GBP" if symbol == "£" else fallback_currency
        amount = parse_number(m.group(2))
        if amount and currency in CURRENCY_CODES:
            return amount, currency

    # 3) Localized prices can be rendered as "563.92 SEK" without a currency symbol.
    # Search fallback-currency occurrences first and skip the face value in the product title.
    pairs: list[tuple[float, str]] = []
    amount_code = re.compile(r"(" + NUMBER + r")\s*(" + CURRENCY_ALT + r")\b", re.I)
    code_amount = re.compile(r"\b(" + CURRENCY_ALT + r")\s*(" + NUMBER + r")", re.I)
    for match in amount_code.finditer(text):
        amount = parse_number(match.group(1))
        currency = match.group(2).upper()
        if amount and currency in CURRENCY_CODES:
            pairs.append((amount, currency))
    for match in code_amount.finditer(text):
        amount = parse_number(match.group(2))
        currency = match.group(1).upper()
        if amount and currency in CURRENCY_CODES:
            pairs.append((amount, currency))

    # Preserve textual order where possible, then prefer the storefront's locale currency.
    if pairs:
        non_face_value = [
            pair for pair in pairs
            if not (
                nominal_amount is not None
                and pair[1] == nominal_currency
                and abs(pair[0] - float(nominal_amount)) < 1e-6
            )
        ]
        pool = non_face_value or pairs
        locale_pairs = [pair for pair in pool if pair[1] == fallback_currency]
        if locale_pairs:
            return locale_pairs[0]
        return pool[0]

    return None


def extract_deals(html: str, region: dict, checked_ms: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    deals: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="/psn-playstation-network-card-"]'):
        parsed = title_and_nominal(anchor)
        if not parsed:
            continue
        title, nominal_amount, nominal_currency = parsed
        if not is_german_psn_card(title, nominal_currency):
            continue
        href = urljoin("https://www.eneba.com/", str(anchor.get("href") or ""))
        parsed_url = urlparse(href)
        if parsed_url.scheme != "https" or parsed_url.hostname not in ("www.eneba.com", "eneba.com"):
            continue
        # Strip campaign/query fragments to deduplicate consistently.
        href = f"https://www.eneba.com{parsed_url.path}"
        if href in seen:
            continue
        seen.add(href)
        best_text = ""
        parent = anchor
        for _ in range(8):
            if parent.parent is None:
                break
            parent = parent.parent
            text = parent.get_text(" ", strip=True)
            if len(text) > 1400:
                break
            if re.search(r"\bfrom\b|\bab\b", text, re.I) and "PlayStation Network Card" in text:
                best_text = text
                if len(text) <= 650:
                    break
        if not best_text:
            best_text = anchor.parent.get_text(" ", strip=True) if anchor.parent else anchor.get_text(" ", strip=True)
        price = price_from_card_text(best_text, region["currency"], nominal_amount, nominal_currency)
        if not price:
            continue
        price_amount, price_currency = price
        deals.append({
            "title": title,
            "href": href,
            "region": f'{region["name"]} (Abruf-IP)',
            "country": region["country"],
            "productCountry": "DE",
            "nominalAmount": nominal_amount,
            "nominalCurrency": nominal_currency,
            "priceAmount": price_amount,
            "priceCurrency": price_currency,
            "refreshedAt": checked_ms,
        })
    return deals


def fetch_region(region: dict) -> dict:
    country = region["country"]
    # The proxy changes only the request exit country; both pages show German PSN cards.
    # The detail page has the denomination picker and catches variants missing in category cards.
    paths = (GERMAN_PSN_DETAIL_URL, GERMAN_PSN_CATEGORY_URL)
    candidates = get_proxy_candidates(country)
    if not candidates:
        return {"country": country, "ok": False, "deals": [], "reason": "Keine Proxy-Kandidaten gefunden"}
    valid_proxies = verify_country_proxies(country, candidates)
    if not valid_proxies:
        return {"country": country, "ok": False, "deals": [], "reason": "Keine erreichbare Proxy-IP aus diesem Land bestätigt"}

    errors: list[str] = []
    for proxy in valid_proxies:
        proxy_url = f"http://{proxy}"
        proxy_cfg = {"http": proxy_url, "https": proxy_url}
        combined: list[dict] = []
        any_page_loaded = False
        for path in paths:
            try:
                response = requests.get(
                    path,
                    proxies=proxy_cfg,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Cache-Control": "no-cache",
                    },
                    timeout=(4, 10),
                    allow_redirects=True,
                )
                if response.status_code != 200:
                    errors.append(f"{urlparse(path).path}: HTTP {response.status_code}")
                    continue
                final = urlparse(response.url)
                if final.scheme != "https" or final.hostname not in ("www.eneba.com", "eneba.com"):
                    errors.append("Unerwartetes Weiterleitungsziel")
                    continue
                if "text/html" not in response.headers.get("content-type", "").lower():
                    errors.append("Eneba lieferte kein HTML")
                    continue
                any_page_loaded = True
                combined.extend(extract_deals(response.text[:3_000_000], region, timestamp_ms()))
            except Exception as exc:
                errors.append(type(exc).__name__)

        if combined:
            # If both pages expose the same variant, keep the lower price (same currency).
            deduped: dict[str, dict] = {}
            for deal in combined:
                key = str(deal.get("href") or "")
                old = deduped.get(key)
                if old is None:
                    deduped[key] = deal
                    continue
                new_currency = str(deal.get("priceCurrency") or "EUR")
                old_currency = str(old.get("priceCurrency") or "EUR")
                new_amount = float(deal.get("priceAmount") or 0)
                old_amount = float(old.get("priceAmount") or 0)
                if new_currency == old_currency and new_amount > 0 and (old_amount <= 0 or new_amount < old_amount):
                    deduped[key] = deal
            return {"country": country, "ok": True, "deals": list(deduped.values()), "reason": ""}

        if any_page_loaded:
            errors.append("Seiten geladen, aber keine deutschen Kartenvarianten erkannt")
    return {
        "country": country,
        "ok": False,
        "deals": [],
        "reason": "; ".join(errors[-4:]) or "Proxyabruf fehlgeschlagen",
    }


def get_site_data_url() -> str | None:
    explicit = os.getenv("SITE_BASE_URL", "").strip()
    if explicit:
        return urljoin(explicit.rstrip("/") + "/", "data.json")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    # Public GitHub Pages API exposes the actual URL, including custom domains.
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{name}/pages", headers={"Accept": "application/vnd.github+json"}, timeout=(3, 5))
        if r.ok:
            base = str(r.json().get("html_url") or "").strip()
            if base:
                return urljoin(base.rstrip("/") + "/", "data.json")
    except Exception:
        pass
    # Common fallback for a project page / a user-site repository.
    base = f"https://{owner}.github.io/" if name.lower() == f"{owner.lower()}.github.io" else f"https://{owner}.github.io/{name}/"
    return urljoin(base, "data.json")


def load_previous_data() -> dict:
    url = get_site_data_url()
    if url:
        try:
            response = requests.get(url, params={"_refresh": int(time.time())}, headers={"Cache-Control": "no-cache"}, timeout=(4, 7))
            if response.ok:
                data = response.json()
                if isinstance(data, dict) and isinstance(data.get("deals"), list):
                    log(f"Vorheriger Datenstand geladen: {len(data.get('deals', []))} Angebote")
                    return data
        except Exception as exc:
            log(f"Vorheriger Datenstand noch nicht erreichbar: {exc}")
    return {}


def select_regions() -> list[dict]:
    priority = [REGION_BY_CODE[c] for c in PRIORITY if c in REGION_BY_CODE]
    other = [r for r in REGIONS if r["country"] not in PRIORITY]
    batches = [other[i:i + COUNTRY_BATCH_SIZE] for i in range(0, len(other), COUNTRY_BATCH_SIZE)]
    try:
        n = int(os.getenv("GITHUB_RUN_NUMBER", "1"))
    except ValueError:
        n = 1
    batch = batches[(n - 1) % len(batches)] if batches else []
    chosen = priority + batch
    log(f"Diese Runde prüft {len(chosen)} Länder: " + ", ".join(r["country"] for r in chosen))
    return chosen


def make_deals_ranked(deals: list[dict], rates: dict[str, float], now_ms: int) -> list[dict]:
    by_key: dict[str, dict] = {}
    for d in deals:
        refreshed = int(d.get("refreshedAt") or 0)
        if not refreshed or (now_ms - refreshed) > DEAL_MAX_AGE_SECONDS * 1000:
            continue
        nominal_eur = to_eur(float(d.get("nominalAmount") or 0), str(d.get("nominalCurrency") or "EUR"), rates)
        price_eur = to_eur(float(d.get("priceAmount") or 0), str(d.get("priceCurrency") or "EUR"), rates)
        if not nominal_eur or not price_eur or nominal_eur <= 0 or price_eur <= 0:
            continue
        d = dict(d)
        d["nominalEur"] = round(nominal_eur, 4)
        d["priceEur"] = round(price_eur, 4)
        d["ratio"] = round(nominal_eur / price_eur, 6)
        d["nominal"] = f'{d.get("nominalAmount")} {d.get("nominalCurrency")}'
        d["price"] = f'{d.get("priceAmount")} {d.get("priceCurrency")}'
        d["refreshedAtIso"] = utc_iso(refreshed)
        key = f"{d.get('href')}|{d.get('country')}"
        if key not in by_key or d["ratio"] > by_key[key]["ratio"]:
            by_key[key] = d
    return sorted(by_key.values(), key=lambda x: x["ratio"], reverse=True)[:20]


def main() -> int:
    now_ms = timestamp_ms()
    previous = load_previous_data()
    rates = get_fx_rates()
    selected = select_regions()
    selected_codes = {r["country"] for r in selected}
    old_deals = previous.get("allDeals", previous.get("deals", []))
    if not isinstance(old_deals, list):
        old_deals = []
    kept_deals = [
        d for d in old_deals
        if isinstance(d, dict)
        and (now_ms - int(d.get("refreshedAt") or 0)) <= DEAL_MAX_AGE_SECONDS * 1000
        and is_german_psn_card(str(d.get("title") or ""), str(d.get("nominalCurrency") or ""))
    ]

    old_status = previous.get("regionStatus") if isinstance(previous.get("regionStatus"), dict) else {}
    statuses = dict(old_status)
    results: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=MAX_REGION_WORKERS) as pool:
        futures = {pool.submit(fetch_region, region): region for region in selected}
        for future in cf.as_completed(futures):
            region = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"country": region["country"], "ok": False, "deals": [], "reason": f"Interner Fehler: {type(exc).__name__}"}
            results.append(result)
            statuses[region["country"]] = {
                "name": region["name"],
                "country": region["country"],
                "checkedAt": now_ms,
                "ok": bool(result["ok"]),
                "dealCount": len(result["deals"]),
                "reason": str(result.get("reason") or ""),
            }
            if result["ok"]:
                kept_deals = [d for d in kept_deals if d.get("country") != region["country"]]
                kept_deals.extend(result["deals"])
            log(f"{region['country']}: {'OK' if result['ok'] else 'nicht verfügbar'} · {len(result['deals'])} Treffer")

    # Recompute latest ranking using current FX data; stale entries are discarded.
    all_deals = []
    for d in kept_deals:
        if (now_ms - int(d.get("refreshedAt") or 0)) <= DEAL_MAX_AGE_SECONDS * 1000:
            all_deals.append(d)
    ranked = make_deals_ranked(all_deals, rates, now_ms)
    succeeded = sum(1 for r in results if r["ok"])
    note = ""
    if not ranked:
        note = "Noch keine bestätigten deutschen PSN-Angebote. Proxies werden nur für den Abruf aus anderen Ländern verwendet; die Karten müssen weiterhin DE/Germany sein. Öffentliche Länder-Proxys sind unzuverlässig; die nächste automatische Aktualisierung erfolgt in etwa fünf Minuten."
    payload = {
        "generatedAt": now_ms,
        "generatedAtIso": utc_iso(now_ms),
        "deals": ranked,
        "allDeals": all_deals,
        "regionCount": len(REGIONS),
        "regionsScannedThisRun": len(selected),
        "regionsSucceededThisRun": succeeded,
        "regionsFailedThisRun": len(selected) - succeeded,
        "regionsSucceeded": sum(1 for s in statuses.values() if s.get("ok") and (now_ms - int(s.get("checkedAt") or 0)) <= DEAL_MAX_AGE_SECONDS * 1000),
        "regionStatus": statuses,
        "productsFoundThisRun": sum(len(r["deals"]) for r in results),
        "note": note,
        "updateIntervalMinutes": 5,
        "dealDataMaxAgeMinutes": DEAL_MAX_AGE_SECONDS // 60,
        "proxySources": ["ProxyScrape", "HProxy", "Databay"],
    }
    DOCS.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    log(f"Daten-Datei erstellt: {len(ranked)} Top-Angebote; {succeeded}/{len(selected)} Länder in dieser Runde erfolgreich")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"Fataler Fehler: {type(exc).__name__}: {exc}")
        # Keep workflow publishing the site even if the scan fails.
        if not DATA_FILE.exists():
            DATA_FILE.write_text(json.dumps({"generatedAt": timestamp_ms(), "deals": [], "regionCount": len(REGIONS), "regionsSucceeded": 0, "note": f"Scan fehlgeschlagen: {type(exc).__name__}. Bitte nächste Aktualisierung abwarten."}, ensure_ascii=False), encoding="utf-8")
        sys.exit(0)
