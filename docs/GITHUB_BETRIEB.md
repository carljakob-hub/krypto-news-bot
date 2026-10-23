# Betrieb kostenlos über GitHub (ohne eigenen Server)

GitHub startet den Bot etwa alle 5–10 Minuten (`.github/workflows/newsbot.yml`). Jeder Lauf prüft ca. 28 Minuten lang im Minutentakt (`newsbot run --max-cycles 28`), die Quellen werden also etwa jede Minute abgefragt.
Datenbank und Beitragsprotokoll werden zwischen den Läufen im Branch `state` gespeichert.

## Zugangsdaten eintragen (einmalig)

Im Repository: **Settings → Secrets and variables → Actions → New repository secret**.
Für jeden Eintrag: oben den Namen, unten den Wert eintragen und auf **Add secret** klicken.

| Name | Wert |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token von BotFather |
| `TELEGRAM_TEST_CHAT_ID` | Nummer der Testgruppe |
| `TELEGRAM_TEST_TOPIC_ID` | Themen-Nummer in der Testgruppe (nur falls sie Themen hat) |
| `TELEGRAM_CHAT_ID` | Nummer der echten Gruppe |
| `TELEGRAM_TOPIC_ID` | Themen-Nummer (z. B. „News“) in der echten Gruppe |
| `TELEGRAM_ADMIN_CHAT_ID` | optional: deine eigene Telegram-Nummer für Warnungen |
| `ANTHROPIC_API_KEY` | optional: für deutsche Texte |

Secrets sind verschlüsselt und für niemanden sichtbar, auch nicht in den Protokollen.

## Von Test auf echte Gruppe umschalten

1. **Settings → Secrets and variables → Actions → Tab „Variables“ → New repository variable**:
   Name `NEWSBOT_MODE`, Wert `production`.
2. **Actions → newsbot → Run workflow** → Befehl `preflight` → **Run workflow**.
   Der Lauf muss grün werden („Preflight BESTANDEN“).
3. Ab dann postet der Bot in die echte Gruppe.

Zurück in den Testmodus: Variable `NEWSBOT_MODE` löschen oder auf `test` setzen.

## Bot anhalten

**Actions → newsbot → „…“ → Disable workflow.** Wieder einschalten: **Enable workflow**.

## Hinweise

- GitHub schaltet geplante Abläufe ab, wenn in einem Repository 60 Tage lang keine Aktivität war. Dann kommt eine E-Mail – einfach **Enable workflow** klicken.
- Geplante Starts können sich bei hoher Last bei GitHub um einige Minuten verzögern.
- Das Repository ist öffentlich (dann unbegrenzt kostenlos). Code, Datenbank und Beitragsprotokoll sind einsehbar, Zugangsdaten nicht.
