# Sichere Konfiguration und Dauerbetrieb

## Geheimnisse

- Zugangsdaten **nur** in `.env` (Rechte `chmod 600`) oder in einem Secret-Manager, der sie als Umgebungsvariablen bereitstellt (z. B. Docker Secrets, systemd `EnvironmentFile`, Vault). `.env` ist in `.gitignore` und `.dockerignore` eingetragen.
- `config/*.yaml` enthält keine Geheimnisse und kann versioniert werden.
- Logs maskieren Telegram-Tokens und API-Schlüssel automatisch; die HTTP-Bibliotheken loggen keine URLs (das Telegram-Token steckt in der URL).
- `Secrets` gibt in `repr()`/`str()` nur `***` aus.
- Beiträge enthalten keine Zugangsdaten; Links werden nur aus geprüften Quellen gesetzt.

## Was das System bewusst nicht kann

- Keine Trades, keine Börsen-API-Schlüssel, kein Zugriff auf Konten.
- Kein Ausführen von Befehlen oder Dateien aus Nachrichten; externe Inhalte werden nur als Text verarbeitet.
- Nur lesende HTTP-GET-Abrufe auf HTTPS-Quellen; Weiterleitungen auf unverschlüsseltes HTTP werden abgelehnt.
- Der Bot liest keine Gruppennachrichten und reagiert auf keine Befehle.

## Container-Härtung (docker-compose.yml)

Unprivilegierter Benutzer (UID 10001), schreibgeschütztes Dateisystem außer `data/`, `config/` nur lesbar, alle Linux-Capabilities entfernt, `no-new-privileges`, Speicher-/CPU-Limit, Log-Rotation, `restart: unless-stopped`, Healthcheck über `newsbot health`.

## Dauerbetrieb

- **Start:** `docker compose up -d`. Bei Absturz oder Server-Neustart startet Docker den Dienst neu.
- **Logs:** `docker compose logs -f --tail 200`
- **Status:** `docker compose exec newsbot newsbot status --hours 24`
- **Belege einer Entscheidung:** `docker compose exec newsbot newsbot explain <ereignis-id>`
- **Beitragsarchiv:** `data/posts.log` (jeder Beitrag, auch im Produktivmodus)
- **Warnungen:** an `TELEGRAM_ADMIN_CHAT_ID` und als `CRITICAL` im Log, mit Abkühlzeit (`alert_cooldown_minutes`) und Entwarnung, sobald das Problem behoben ist. Ausgelöst bei: keine erfolgreiche Recherche seit `alert_no_successful_research_minutes`, mehrere fehlgeschlagene Durchläufe in Folge, mehrere fehlgeschlagene Veröffentlichungen in Folge, ungültiges Telegram-Token.
- **Externe Überwachung (empfohlen):** Den Docker-Healthstatus (`docker inspect --format '{{.State.Health.Status}}' krypto-news-bot`) z. B. per Cron oder Uptime-Kuma prüfen – das fängt auch den Fall ab, dass der Server selbst ausfällt.

## Schutz gegen Fluten und Endlosschleifen

- `max_posts_per_hour`, `max_posts_per_day`, `min_seconds_between_posts` (HOCH darf den Mindestabstand, nicht die Stundengrenze überspringen).
- UNIQUE-Bedingung: ein Ereignis wird höchstens einmal veröffentlicht; zusätzlich Ähnlichkeitsprüfung gegen Beiträge der letzten 72 h.
- Fehlgeschlagene Sendungen: höchstens `max_send_attempts` mit wachsendem Abstand; Beiträge verfallen nach `max_queue_age_hours`, damit keine veralteten News nachgereicht werden.
- Pro Durchlauf: Zeitlimit, max. 40 Meldungen je Quelle, max. 100 bewertete Ereignisse, max. 15 Dokumentabrufe; Ereignisse werden nach `pending_reevaluation_hours` nicht mehr neu bewertet.
- Scheduler mit exponentiellem Backoff bis `max_backoff_seconds` nach Fehlern.

## Updates

```bash
git pull                     # bzw. neue Version kopieren
docker compose build
docker compose run --rm newsbot pytest -q -p no:cacheprovider
docker compose up -d
```

Ändert sich `config/` oder die Zielgruppe, ist der Preflight ungültig und der Produktivmodus startet erst nach erneutem `newsbot preflight` (siehe Checkliste). Das ist Absicht.

## Backups

Die gesamte Historie liegt in `data/newsbot.sqlite3`. Sicherung im laufenden Betrieb:

```bash
sqlite3 data/newsbot.sqlite3 ".backup 'backup-$(date +%F).sqlite3'"
```

## Quellen pflegen

- Neue Quelle in `config/sources.yaml` mit ehrlicher `trust`-Einstufung und korrekter `owner_group` (Medien desselben Verlags = gleiche Gruppe!).
- Nutzungsbedingungen der Anbieter beachten (Abrufhäufigkeit, kommerzielle Nutzung).
- `newsbot check-sources` nach jeder Änderung.

## Kosten im Blick

- `llm.daily_budget_usd` begrenzt die Ausgaben hart (rollierend 24 h).
- `llm.model` lässt sich z. B. auf `claude-haiku-5-5` umstellen; dann die Preise `price_input_usd_per_mtok` / `price_output_usd_per_mtok` anpassen (0.10 / 0.50), damit das Monitoring stimmt.
- `llm.enabled: false` schaltet das Sprachmodell vollständig ab.
