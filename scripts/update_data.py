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
