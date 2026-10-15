#!/usr/bin/env python3
"""Run the existing Eneba scanner with a Chromium-rendered DOM.

The existing update_data.py parser already reads Eneba's native ratio text from
.ANNnf5 and its visible variant price from .L5ErLT.w7tD3i. This wrapper makes
sure the parser receives the rendered browser DOM rather than the pre-hydration
HTML returned by requests.get().
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_data.py"
SPEC = importlib.util.spec_from_file_location("psn_existing_scanner", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Kann Scanner nicht laden: {SCRIPT}")
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


def fetch_region_browser(region: dict) -> dict:
    country = region["country"]
    paths = (scanner.GERMAN_PSN_DETAIL_URL, scanner.GERMAN_PSN_CATEGORY_URL)
    candidates = scanner.get_proxy_candidates(country)
    if not candidates:
        return {"country": country, "ok": False, "deals": [], "reason": "Keine Proxy-Kandidaten gefunden"}

    proxies = scanner.verify_country_proxies(country, candidates)
    if not proxies:
        return {"country": country, "ok": False, "deals": [], "reason": "Keine erreichbare Proxy-IP aus diesem Land bestätigt"}

    errors: list[str] = []
    for proxy in proxies:
        proxy_server = f"http://{proxy}"
        combined: list[dict] = []
        browser = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    proxy={"server": proxy_server},
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(
                    user_agent=scanner.USER_AGENT,
                    locale="en-US",
                    viewport={"width": 1440, "height": 1000},
                    ignore_https_errors=True,
                )
                page = context.new_page()
                page.set_default_navigation_timeout(22_000)

                for path in paths:
                    try:
                        response = page.goto(path, wait_until="domcontentloaded", timeout=22_000)
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

                        # Wait for the two Eneba-native fields we rank by: ratio + price.
                        try:
                            page.wait_for_function(
                                """() => Array.from(document.querySelectorAll('a[href*="/psn-playstation-network-card-"]'))
                                  .some(a => a.querySelector('.ANNnf5') && a.querySelector('.L5ErLT.w7tD3i'))""",
                                timeout=12_000,
                            )
                        except PlaywrightTimeoutError:
                            page.wait_for_timeout(1_000)

                        html = page.content()
                        product_count = page.locator('a[href*="/psn-playstation-network-card-"]').count()
                        ratio_count = page.locator('a[href*="/psn-playstation-network-card-"] .ANNnf5').count()
                        price_count = page.locator('a[href*="/psn-playstation-network-card-"] .L5ErLT.w7tD3i').count()
                        deals = scanner.extract_deals(html[:4_000_000], region, scanner.timestamp_ms())
                        scanner.log(
                            f"{country}: Browser-DOM {urlparse(path).path}: "
                            f"Produkte={product_count}, Eneba-Ratios={ratio_count}, Preise={price_count}, "
                            f"DE-Treffer={len(deals)}"
                        )
                        combined.extend(deals)
                        if deals:
                            break
                        title = (page.title() or "")[:100]
                        if product_count == 0:
                            errors.append(f"{urlparse(path).path}: keine Produktkarten (Seitentitel: {title})")
                        elif ratio_count == 0 or price_count == 0:
                            errors.append(
                                f"{urlparse(path).path}: native Ratio/Preis-Elemente fehlen "
                                f"(Ratio={ratio_count}, Preis={price_count})"
                            )
                        else:
                            errors.append(f"{urlparse(path).path}: Karten da, Filter/Parser erkannte keine DE-Karten")
                    except PlaywrightTimeoutError:
                        errors.append(f"{urlparse(path).path}: Browser-Timeout")
                    except Exception as exc:
                        errors.append(f"{urlparse(path).path}: {type(exc).__name__}: {exc}")

                context.close()
        except Exception as exc:
            errors.append(f"Chromium/Proxy {type(exc).__name__}: {exc}")
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

        if combined:
            # When the category and detail page contain the same denomination,
            # keep the higher ORIGINAL Eneba ratio; do not recalculate it.
            deduped: dict[str, dict] = {}
            for deal in combined:
                key = str(deal.get("href") or "")
                old = deduped.get(key)
                if old is None or float(deal.get("ratio") or 0) > float(old.get("ratio") or 0):
                    deduped[key] = deal
            return {"country": country, "ok": True, "deals": list(deduped.values()), "reason": ""}

    return {
        "country": country,
        "ok": False,
        "deals": [],
        "reason": "; ".join(errors[-5:]) or "Kein Eneba-Wertverhältnis im gerenderten DOM gefunden",
    }


# Swap only the fetcher. Ranking/data retention/site output continue to use the
# project's existing main() and are intentionally unchanged.
scanner.fetch_region = fetch_region_browser

if __name__ == "__main__":
    try:
        sys.exit(scanner.main())
    except Exception as exc:
        scanner.log(f"Fataler Browser-Scan-Fehler: {type(exc).__name__}: {exc}")
        sys.exit(1)
