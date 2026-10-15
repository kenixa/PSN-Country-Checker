# Eneba PSN Top 20 — kostenlos veröffentlichen und alle 5 Minuten aktualisieren

Diese Variante ist für GitHub Pages + GitHub Actions gebaut. Es gibt keinen Node-Server, kein Render-Abo und keine bezahlte Proxy-API. GitHub Actions erstellt regelmäßig eine neue `docs/data.json` und veröffentlicht die Website als statische Seite.

## Was kostenlos ist

- **Hosting:** GitHub Pages in einem öffentlichen GitHub-Repository.
- **Automatische Aktualisierung:** GitHub Actions alle 5 Minuten (`*/5 * * * *`).
- **Proxys:** mehrere öffentliche, kostenlose Proxy-Listen ohne API-Key.
- **Einkauf:** Links öffnen Eneba direkt im normalen Browser. Proxyserver werden nur für anonyme öffentliche GET-Abrufe verwendet; keine Eneba-Cookies, Anmeldung oder Warenkorb-Daten werden weitergereicht.

GitHub weist darauf hin, dass geplante Workflows bei Auslastung verzögert werden können. Das bedeutet: fünf Minuten sind der gewünschte Takt, aber keine sekundengenaue SLA.

## Veröffentlichung — einmalig

1. Lade den **gesamten Inhalt dieses Ordners** in ein neues GitHub-Repository hoch. Es muss bei einem kostenlosen GitHub-Konto **öffentlich** sein, damit GitHub Pages kostenlos verfügbar ist.
2. Öffne im Repository den Reiter **Actions**.
3. Öffne **Settings → Pages** und wähle unter **Build and deployment → Source** den Eintrag **GitHub Actions**. Diese einmalige Einstellung ist erforderlich; der Workflow kann das nicht sicher mit dem normalen `GITHUB_TOKEN` für dich freischalten.
4. Öffne den Reiter **Actions**, wähle **PSN Top 20 aktualisieren und veröffentlichen** und klicke auf **Run workflow**. Der Workflow scannt Länder und veröffentlicht die Website.
5. Nach einem erfolgreichen Lauf findest du die öffentliche URL unter **Settings → Pages** oder im Job `deploy` als `page_url`.

Danach startet der geplante Workflow alle fünf Minuten automatisch. Die Schaltfläche ↻ auf der Website lädt nur die zuletzt veröffentlichte Datendatei erneut; sie löst aus Sicherheitsgründen keinen GitHub-Workflow direkt aus.

## Länder- und Aktualisierungsverhalten

Australien (AU), Niederlande (NL) und Deutschland (DE) werden bei jedem Lauf priorisiert. Zusätzlich rotiert der Scanner durch weitere Länder. So wird die `data.json` alle fünf Minuten neu veröffentlicht, ohne bei jedem Lauf alle 53 Regionen parallel mit hunderten Proxyversuchen zu überlasten. Die restlichen Regionen werden über mehrere Läufe verteilt geprüft. Angebote älter als 60 Minuten werden entfernt.

Der Scanner verwendet drei kostenlose Listenquellen:

- ProxyScrape: länderbezogene HTTP-Proxylisten
- HProxy: länderbezogene öffentliche Proxylisten
- Databay Labs: länderbezogene öffentliche Proxylisten

Ein Proxy wird erst verwendet, wenn seine tatsächliche Exit-IP beim öffentlichen IP-Geolokalisierungsdienst als das angefragte Land erkannt wird. Anschließend wird nur eine öffentliche Eneba-Kategorieseite per HTTPS-GET abgerufen. Wenn ein Proxy ausfällt oder Eneba ihn blockiert, versucht der Scanner andere Kandidaten.

## Ehrliche Einschränkungen

Kostenlose öffentliche Proxys sind **nicht zuverlässig**: Sie können nach wenigen Minuten ausfallen, falsche Geo-IP-Daten liefern oder von Eneba blockiert werden. Wenn Australien oder die Niederlande aktuell keinen erreichbaren verifizierten Proxy haben, erscheinen dafür keine frischen Ergebnisse. Die Website gibt solche Länder nicht fälschlich als erfolgreich aus.

Die Extraktion arbeitet mit öffentlich gerenderten PSN-Kategorieseiten und erkennbaren Produktkarten/Preisen. Eneba kann die HTML-Struktur jederzeit ändern; die Schaltfläche „weitere Händlerangebote“ auf jeder einzelnen Produktdetailseite wird nicht garantiert vollständig ausgelesen. Vorhandene Angebote werden nach **Guthabenwert in EUR / Kaufpreis in EUR** sortiert; der Vergleich garantiert keine Einlösbarkeit in deinem PSN-Konto, denn Guthabenkarten sind häufig regionsgebunden.

## Scan manuell testen

Auf GitHub: **Actions → PSN Top 20 aktualisieren und veröffentlichen → Run workflow**. Öffne danach das Log von `scan-and-build`. Dort steht für jede Region, ob ein Proxy bestätigt wurde und ob Eneba-Angebote extrahiert wurden.

## Verzeichnis

- `docs/index.html`, `docs/style.css`, `docs/app.js`: statische Website
- `docs/data.json`: aktuell veröffentlichte Datenstruktur / Startdatei
- `scripts/update_data.py`: Proxyprüfung und Preisvergleich
- `scripts/regions.json`: 53 Länder-Routen
- `.github/workflows/update-and-publish.yml`: Zeitplan und GitHub-Pages-Veröffentlichung
