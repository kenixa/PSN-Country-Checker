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
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

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
        # Official public ProxyScrape API, filtered to HTTP proxies and target country.
        (
            "https://api.proxyscrape.com/v4/free-proxy-list/get"
            f"?request=display_proxies&proxy_format=protocolipport&format=text&country={cc}&protocol=http"
        ),
        # Official ProxyScrape mirror/CDN, country + HTTP protocol shard.
        f"https://cdn.jsdelivr.net/gh/proxyscrape/free-proxy-list@main/proxies/countries/{cc}/http/data.txt",
        f"https://raw.githubusercontent.com/ProxyScrape/free-proxy-list/main/proxies/countries/{cc}/http/data.txt",
        f"https://raw.githubusercontent.com/hproxy-com/free-proxy-list/main/by-country/{country}.txt",
        f"https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/by-country/{cc}/http.txt",
        # Proxifly's country shard may contain mixed protocols; explicit non-HTTP schemes are filtered below.
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
                line = line.strip()
                scheme_match = re.match(r"^(https?|socks4|socks5)://", line, re.I)
                if scheme_match and scheme_match.group(1).lower() != "http":
                    # Requests below are configured for plain HTTP proxies only.
                    continue
                m = re.search(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3}):(\d{1,5})(?!\d)", line)
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
            if got == expected_country:
                return proxy, got
            # Try the next GeoIP provider before rejecting this proxy.
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
    """Read the full variant label from Eneba's own denomination-picker card."""
    title = (
        str(anchor.get("aria-label") or "").strip()
        or str(anchor.get("title") or "").strip()
    )
    if not title:
        img = anchor.find("img", alt=True)
        title = str(img.get("alt") or "").strip() if img else ""
    title = title or anchor.get_text(" ", strip=True)
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


def parse_eneba_ratio(anchor) -> tuple[float, str, str] | None:
    """Read Eneba's displayed ratio text (for example '1.26 EUR per €1').

    We intentionally do not derive this from the price or any FX service.
    """
    node = anchor.select_one(".ANNnf5")
    candidates = [node] if node else []
    if not candidates:
        candidates = anchor.find_all("span")
    pattern = re.compile(
        r"^\s*(?P<value>\d+(?:[.,]\d+)?)\s+(?P<unit>[A-Z]{3})\s+per\s+€\s*1\s*$",
        re.I,
    )
    for candidate in candidates:
        source_text = candidate.get_text(" ", strip=True)
        match = pattern.match(source_text)
        if not match:
            continue
        value = parse_number(match.group("value"))
        if value is not None:
            # ratio_display is the exact number string shown by Eneba (decimal point/comma preserved).
            return value, match.group("value"), source_text
    return None


def parse_eneba_price_display(raw: str, fallback_currency: str) -> tuple[float, str] | None:
    """Parse only the price string Eneba displays, retaining its original display separately."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        return None

    # Currency symbol before amount (most common on Eneba); longer symbols first.
    symbol_pattern = re.compile(
        r"^(?P<symbol>AU\$|A\$|CA\$|C\$|US\$|NZ\$|€|£|\$)\s*(?P<amount>" + NUMBER + r")$",
        re.I,
    )
    match = symbol_pattern.match(text)
    if match:
        symbol = match.group("symbol").upper()
        currency_map = {
            "€": "EUR", "£": "GBP", "AU$": "AUD", "A$": "AUD",
            "CA$": "CAD", "C$": "CAD", "US$": "USD", "NZ$": "NZD",
            "$": str(fallback_currency or "EUR").upper(),
        }
        amount = parse_number(match.group("amount"))
        currency = currency_map.get(symbol, str(fallback_currency or "EUR").upper())
        return (amount, currency) if amount and currency in CURRENCY_CODES else None

    # Or a currency code before/after the amount, for example '76.53 AUD' or 'EUR 47.71'.
    code_after = re.compile(r"^(?P<amount>" + NUMBER + r")\s*(?P<currency>" + CURRENCY_ALT + r")$", re.I)
    code_before = re.compile(r"^(?P<currency>" + CURRENCY_ALT + r")\s*(?P<amount>" + NUMBER + r")$", re.I)
    for pattern in (code_after, code_before):
        match = pattern.match(text)
        if match:
            amount = parse_number(match.group("amount"))
            currency = match.group("currency").upper()
            return (amount, currency) if amount and currency in CURRENCY_CODES else None
    return None


def eneba_price_from_variant(anchor, fallback_currency: str) -> tuple[float, str, str] | None:
    """Use the exact price node from Eneba's value-picker card, not surrounding text."""
    node = anchor.select_one(".L5ErLT.w7tD3i")
    if node is None:
        # Conservative structural fallback: accept only a span containing a stand-alone price label.
        price_re = re.compile(
            r"^(?:(?:AU|A|CA|C|US|NZ)\$|€|£|\$)\s*" + NUMBER +
            r"$|^" + NUMBER + r"\s*(?:" + CURRENCY_ALT + r")$|^(?:" + CURRENCY_ALT + r")\s*" + NUMBER + r"$",
            re.I,
        )
        for span in anchor.find_all("span"):
            text = span.get_text(" ", strip=True)
            if not text or "per" in text.lower() or "cashback" in text.lower():
                continue
            if price_re.match(text):
                node = span
                break
    if node is None:
        return None
    display = node.get_text(" ", strip=True)
    parsed = parse_eneba_price_display(display, fallback_currency)
    if not parsed:
        return None
    amount, currency = parsed
    return amount, currency, display


def extract_deals(html: str, region: dict, checked_ms: int) -> list[dict]:
    """Extract German PSN denominations and the ratio printed by Eneba itself."""
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

        ratio = parse_eneba_ratio(anchor)
        price = eneba_price_from_variant(anchor, region["currency"])
        # No guessing: only list a card if Eneba itself exposes both the ratio and price.
        if not ratio or not price:
            continue

        href = urljoin("https://www.eneba.com/", str(anchor.get("href") or ""))
        parsed_url = urlparse(href)
        if parsed_url.scheme != "https" or parsed_url.hostname not in ("www.eneba.com", "eneba.com"):
            continue
        href = f"https://www.eneba.com{parsed_url.path}"
        if href in seen:
            continue
        seen.add(href)

        ratio_value, ratio_display, ratio_text = ratio
        price_amount, price_currency, price_display = price
        best_value_badge = bool(re.search(r"\bBest\s+value\b", anchor.get_text(" ", strip=True), re.I))
        deals.append({
            "title": title,
            "href": href,
            "isBestValue": best_value_badge,
            "region": f'{region["name"]} (Abruf-IP)',
            "country": region["country"],
            "productCountry": "DE",
            "nominalAmount": nominal_amount,
            "nominalCurrency": nominal_currency,
            "priceAmount": price_amount,
            "priceCurrency": price_currency,
            "priceDisplay": price_display,
            "price": price_display,
            "ratio": ratio_value,
            "ratioDisplay": ratio_display,
            "ratioText": ratio_text,
            "ratioSource": "eneba_display",
            "refreshedAt": checked_ms,
        })
    return deals


def fetch_region(region: dict) -> dict:
    """Render Eneba like a real browser, then read only its displayed ratio and price."""
    country = region["country"]
    paths = (GERMAN_PSN_DETAIL_URL, GERMAN_PSN_CATEGORY_URL)
    candidates = get_proxy_candidates(country)
    if not candidates:
        return {"country": country, "ok": False, "deals": [], "reason": "Keine Proxy-Kandidaten gefunden"}

    valid_proxies = verify_country_proxies(country, candidates)
    if not valid_proxies:
        return {
            "country": country,
            "ok": False,
            "deals": [],
            "reason": "Keine erreichbare Proxy-IP aus diesem Land bestätigt",
        }

    errors: list[str] = []
    for proxy in valid_proxies:
        proxy_url = f"http://{proxy}"
        combined: list[dict] = []
        try:
            # requests.get() can receive pre-hydration HTML without the visible variant picker.
            # Chromium executes Eneba's page scripts, so the parser sees the same UI values.
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    proxy={"server": proxy_url},
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                try:
                    context = browser.new_context(
                        user_agent=USER_AGENT,
                        locale="en-US",
                        viewport={"width": 1440, "height": 1000},
                        ignore_https_errors=True,
                    )
                    page = context.new_page()
                    page.set_default_navigation_timeout(25_000)

                    for path in paths:
                        try:
                            response = page.goto(path, wait_until="domcontentloaded", timeout=25_000)
                            if response is None:
                                errors.append(f"{urlparse(path).path}: keine HTTP-Antwort")
                                continue
                            if response.status != 200:
                                errors.append(f"{urlparse(path).path}: HTTP {response.status}")
                                continue

                            final = urlparse(page.url)
                            if final.scheme != "https" or final.hostname not in ("www.eneba.com", "eneba.com"):
                                errors.append("Unerwartetes Weiterleitungsziel")
                                continue

                            # Wait for the real, client-rendered denomination tiles. Class names
                            # are only a wait hint; extract_deals also has structural fallbacks.
                            try:
                                page.wait_for_function(
                                    """() => Array.from(document.querySelectorAll('a[href*="/psn-playstation-network-card-"]'))
                                      .some(a => a.querySelector('.ANNnf5') && a.querySelector('.L5ErLT.w7tD3i'))""",
                                    timeout=9_000,
                                )
                            except PlaywrightTimeoutError:
                                page.wait_for_timeout(1_200)

                            html = page.content()
                            anchor_count = page.locator('a[href*="/psn-playstation-network-card-"]').count()
                            ratio_count = page.locator('a[href*="/psn-playstation-network-card-"] .ANNnf5').count()
                            page_deals = extract_deals(html[:4_000_000], region, timestamp_ms())
                            log(
                                f"{country}: Eneba DOM {urlparse(path).path} · "
                                f"{anchor_count} Produktlinks · {ratio_count} Ratio-Labels · "
                                f"{len(page_deals)} deutsche Angebote"
                            )
                            combined.extend(page_deals)
                            # The detail-page value picker lists denominations. Avoid a redundant
                            # category request if it already yielded direct Eneba ratios.
                            if page_deals:
                                break
                        except PlaywrightTimeoutError:
                            errors.append(f"{urlparse(path).path}: Browser-Timeout")
                        except Exception as exc:
                            errors.append(f"{urlparse(path).path}: {type(exc).__name__}")

                    context.close()
                finally:
                    browser.close()
        except Exception as exc:
            errors.append(f"Browser/Proxy {type(exc).__name__}")

        if combined:
            # Deduplicate repeated denomination links without recalculating Eneba's ratio.
            deduped: dict[str, dict] = {}
            for deal in combined:
                key = str(deal.get("href") or "")
                old = deduped.get(key)
                if old is None or float(deal.get("ratio") or 0) > float(old.get("ratio") or 0):
                    deduped[key] = deal
            return {"country": country, "ok": True, "deals": list(deduped.values()), "reason": ""}

    reason = "; ".join(errors[-5:]) or "Kein Eneba-Preisverhältnis direkt im gerenderten HTML gefunden"
    return {"country": country, "ok": False, "deals": [], "reason": reason}


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
    """Always scan only Australia, the Netherlands and Germany."""
    chosen = [REGION_BY_CODE[c] for c in PRIORITY if c in REGION_BY_CODE]
    log("Überwacht ausschließlich diese Länder: " + ", ".join(r["country"] for r in chosen))
    return chosen


def make_deals_ranked(deals: list[dict], now_ms: int) -> list[dict]:
    by_key: dict[str, dict] = {}
    for source in deals:
        refreshed = int(source.get("refreshedAt") or 0)
        if not refreshed or (now_ms - refreshed) > DEAL_MAX_AGE_SECONDS * 1000:
            continue
        if source.get("ratioSource") != "eneba_display":
            continue
        if not is_german_psn_card(str(source.get("title") or ""), str(source.get("nominalCurrency") or "")):
            continue
        try:
            ratio_value = float(source.get("ratio"))
            nominal_amount = float(source.get("nominalAmount"))
        except (TypeError, ValueError):
            continue
        if ratio_value <= 0 or nominal_amount <= 0:
            continue
        d = dict(source)
        # Germany-only card face value is EUR; this is a denomination label, not an FX conversion.
        d["nominalEur"] = nominal_amount
        d["nominal"] = f'{source.get("nominalAmount")} {source.get("nominalCurrency")}'
        d["price"] = str(source.get("priceDisplay") or source.get("price") or "")
        d["ratio"] = ratio_value  # exact numeric value parsed from Eneba's displayed ratio
        d["ratioDisplay"] = str(source.get("ratioDisplay") or ratio_value)
        d["ratioText"] = str(source.get("ratioText") or f'{d["ratioDisplay"]} EUR per €1')
        d["refreshedAtIso"] = utc_iso(refreshed)
        key = f'{d.get("href")}|{d.get("country")}'
        if key not in by_key or ratio_value > float(by_key[key].get("ratio") or 0):
            by_key[key] = d
    # Eneba rounds the visible ratio to two decimals. If multiple cards tie on that
    # displayed number, prefer Eneba's own "Best value" badge instead of inventing
    # extra precision from a price or exchange rate.
    return sorted(
        by_key.values(),
        key=lambda item: (float(item.get("ratio") or 0), bool(item.get("isBestValue"))),
        reverse=True,
    )[:20]


def main() -> int:
    now_ms = timestamp_ms()
    previous = load_previous_data()
    selected = select_regions()
    selected_codes = {r["country"] for r in selected}
    old_deals = previous.get("allDeals", previous.get("deals", []))
    if not isinstance(old_deals, list):
        old_deals = []
    kept_deals = [
        d for d in old_deals
        if isinstance(d, dict)
        and (now_ms - int(d.get("refreshedAt") or 0)) <= DEAL_MAX_AGE_SECONDS * 1000
        and str(d.get("country") or "") in selected_codes
        and is_german_psn_card(str(d.get("title") or ""), str(d.get("nominalCurrency") or ""))
        and d.get("ratioSource") == "eneba_display"
        and d.get("ratioDisplay") is not None
    ]

    old_status = previous.get("regionStatus") if isinstance(previous.get("regionStatus"), dict) else {}
    statuses = {code: status for code, status in old_status.items() if code in selected_codes}
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
            detail = "" if result["ok"] else f" · Grund: {result.get('reason') or 'unbekannt'}"
            log(f"{region['country']}: {'OK' if result['ok'] else 'nicht verfügbar'} · {len(result['deals'])} Treffer{detail}")

    # Rank by the ratio value printed on Eneba's own card; stale/legacy FX-derived entries are discarded.
    all_deals = []
    for d in kept_deals:
        if (now_ms - int(d.get("refreshedAt") or 0)) <= DEAL_MAX_AGE_SECONDS * 1000:
            all_deals.append(d)
    ranked = make_deals_ranked(all_deals, now_ms)
    succeeded = sum(1 for r in results if r["ok"])
    note = ""
    if not ranked:
        note = "Noch keine bestätigten deutschen PSN-Angebote mit einem direkt von Eneba ausgelesenen Preisverhältnis. Es werden keine Verhältnisse aus Wechselkursen nachgerechnet; die nächste Aktualisierung folgt nach dem Workflow-Zeitplan."
    payload = {
        "generatedAt": now_ms,
        "generatedAtIso": utc_iso(now_ms),
        "deals": ranked,
        "allDeals": all_deals,
        "regionCount": len(PRIORITY),
        "regionsScannedThisRun": len(selected),
        "regionsSucceededThisRun": succeeded,
        "regionsFailedThisRun": len(selected) - succeeded,
        "regionsSucceeded": sum(1 for s in statuses.values() if s.get("ok") and (now_ms - int(s.get("checkedAt") or 0)) <= DEAL_MAX_AGE_SECONDS * 1000),
        "regionStatus": statuses,
        "productsFoundThisRun": sum(len(r["deals"]) for r in results),
        "note": note,
        "updateIntervalMinutes": 5,
        "dealDataMaxAgeMinutes": DEAL_MAX_AGE_SECONDS // 60,
        "proxySources": ["ProxyScrape API", "ProxyScrape CDN", "HProxy", "Databay", "Proxifly"],
        "ratioSource": "Eneba-Webseitenanzeige (kein eigenes FX-Ratio)",
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
            DATA_FILE.write_text(json.dumps({"generatedAt": timestamp_ms(), "deals": [], "regionCount": len(PRIORITY), "regionsSucceeded": 0, "note": f"Scan fehlgeschlagen: {type(exc).__name__}. Bitte nächste Aktualisierung abwarten."}, ensure_ascii=False), encoding="utf-8")
        sys.exit(0)
