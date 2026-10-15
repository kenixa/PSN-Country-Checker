## Schnellstart

1. Alles aus diesem Ordner in ein **öffentliches** GitHub-Repository hochladen.
2. In `Settings → Pages → Build and deployment → Source` **GitHub Actions** auswählen.
3. `Actions` öffnen und `PSN Top 20 aktualisieren und veröffentlichen` → `Run workflow` wählen.
4. Unter `Settings → Pages` die fertige Website-Adresse öffnen.

Der Zeitplan ist `*/5 * * * *`. Die Workflowdatei muss im Standardbranch liegen. GitHub kann geplante Läufe bei hoher Auslastung verzögern.
