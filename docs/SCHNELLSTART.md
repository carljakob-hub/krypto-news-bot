# Schnellstart: in 3 Teilen zum laufenden Bot

Dauer: ca. 30–45 Minuten. Du brauchst Telegram auf dem Handy und einen Computer.

---

## Teil 1 – Telegram vorbereiten (Handy, ca. 10 Min.)

1. **Bot erstellen:** In Telegram nach **@BotFather** suchen (blauer Haken), `/newbot` senden.
   - Namen eingeben, z. B. `Krypto News`
   - Benutzernamen eingeben, muss auf `bot` enden, z. B. `jakob_kryptonews_bot`
   - BotFather schickt dir das **Token** (sieht aus wie `123456789:AAH…`). **Geheim halten!**
2. Bei BotFather `/setprivacy` senden → deinen Bot wählen → **Enable**.
3. **Zwei Gruppen anlegen:**
   - „Krypto News TEST“ (nur du) – hier landen zuerst die Testbeiträge
   - deine echte Zielgruppe
4. **Bot in beide Gruppen holen:** Gruppe → Mitglieder hinzufügen → Bot-Namen suchen → danach den Bot zum **Administrator** machen und nur **„Nachrichten senden“** aktiv lassen.
5. **Gruppen-IDs herausfinden:**
   - In jede Gruppe den Befehl `/start@jakob_kryptonews_bot` schreiben (mit deinem Bot-Namen)
   - Schreibe dem Bot auch privat `/start` (für Warnmeldungen an dich)
   - Im Browser öffnen (dein Token einsetzen):
     `https://api.telegram.org/bot<DEIN_TOKEN>/getUpdates`
   - Dort stehen Einträge wie `"chat":{"id":-1001234567890,"title":"Krypto News TEST"…`
   - Notiere: ID der Testgruppe, ID der Zielgruppe (beginnen mit `-100`) und deine eigene ID (positive Zahl, aus dem privaten Chat)
   - **Gruppe mit Themen (z. B. ein Thema „News“)?** Eine Nachricht im Thema „News“ lange drücken → **Link kopieren**. Der Link sieht so aus: `https://t.me/c/1234567890/12/345`. Die **mittlere Zahl (12)** ist die Themen-Nummer. Die erste Zahl mit `-100` davor ist die Gruppen-ID: `-1001234567890`.
6. Danach bei BotFather `/setjoingroups` → deinen Bot → **Disable** (niemand kann ihn mehr in fremde Gruppen holen).

## Teil 2 – Optional, aber empfohlen: deutsche Texte

Ohne Sprachmodell übernimmt der Bot den Text der Quelle (meist Englisch).
Für deutsche Zusammenfassungen: auf **console.anthropic.com** registrieren → Guthaben aufladen (5–10 $ reichen lange) → **API Keys** → Schlüssel erstellen (beginnt mit `sk-ant-`).

## Teil 3 – Server einrichten (ca. 20 Min.)

Der Bot muss auf einem Rechner laufen, der **rund um die Uhr an** ist. Am einfachsten ist ein kleiner Mietserver (VPS), z. B. bei Hetzner, Netcup oder IONOS, mit **Ubuntu 24.04** und 1–2 GB RAM (ca. 4–6 € im Monat). Nach dem Bestellen bekommst du eine **IP-Adresse** und ein **Root-Passwort**.

### 3.1 Projekt auf den Server kopieren

Auf deinem Computer (Terminal unter Mac/Linux, PowerShell unter Windows), im Ordner mit der ZIP-Datei:

```bash
scp krypto-news-bot.zip root@DEINE-IP:~
ssh root@DEINE-IP
```

### 3.2 Auf dem Server: Docker installieren und entpacken

```bash
apt update && apt install -y unzip
curl -fsSL https://get.docker.com | sh
unzip krypto-news-bot.zip && cd krypto-news-bot
```

### 3.3 Zugangsdaten eintragen

```bash
cp .env.example .env
nano .env
```

Ausfüllen (Rest so lassen):

```
NEWSBOT_MODE=test
TELEGRAM_BOT_TOKEN=123456789:AAH...
TELEGRAM_TEST_CHAT_ID=-100...        # Testgruppe
TELEGRAM_CHAT_ID=-100...             # echte Zielgruppe
TELEGRAM_TOPIC_ID=12                 # nur bei Gruppen mit Themen: Nummer des Themas "News"
TELEGRAM_TEST_TOPIC_ID=              # dasselbe für die Testgruppe (leer lassen, wenn sie keine Themen hat)
TELEGRAM_ADMIN_CHAT_ID=12345678      # deine eigene ID
ANTHROPIC_API_KEY=sk-ant-...         # optional
```

Speichern: `Strg+O`, `Enter`, `Strg+X`. Dann:

```bash
chmod 600 .env
mkdir -p data && chown 10001:10001 data
```

### 3.4 Bauen, prüfen, starten

```bash
docker compose build
docker compose run --rm newsbot newsbot check-sources
docker compose run --rm newsbot newsbot check-telegram --send-test
docker compose up -d
```

- `check-sources` zeigt, welche Nachrichtenquellen erreichbar sind (✗ = in `config/sources.yaml` auf `enabled: false` setzen).
- `check-telegram --send-test` muss überall ✓ zeigen; in der Testgruppe kommt „NewsBot-Test: Verbindung funktioniert“.
- Ab `up -d` läuft der Bot dauerhaft **im Testmodus**: Beiträge erscheinen nur in der **Testgruppe**.

Nützlich:

```bash
docker compose logs -f --tail 100              # was passiert gerade (Strg+C beendet nur die Anzeige)
docker compose exec newsbot newsbot status     # Statistik
```

## Teil 4 – Nach ein paar Tagen Test: echte Gruppe einschalten

Wenn dir die Beiträge in der Testgruppe gefallen:

```bash
cd ~/krypto-news-bot
nano .env                                       # NEWSBOT_MODE=production setzen
docker compose run --rm newsbot newsbot preflight
docker compose up -d
```

Der Preflight muss mit **„BESTANDEN – Produktivbetrieb freigegeben“** enden. Ab dann postet der Bot selbstständig in deine Zielgruppe. Nach jeder Änderung an `config/` den Preflight wiederholen.

---

### Häufige Probleme

| Meldung | Lösung |
|---|---|
| `Chat … nicht erreichbar` | Bot ist nicht in der Gruppe oder die ID ist falsch (Minus-Zeichen vergessen?) |
| `In der Gruppe dürfen nur Admins schreiben` | Bot zum Administrator machen |
| `Token ungültig` | Token neu von BotFather kopieren, `.env` korrigieren, `docker compose up -d` |
| `Produktivbetrieb verweigert` | `newsbot preflight` ausführen (siehe Teil 4) |
| `permission denied` bei `data` | `chown 10001:10001 data` erneut ausführen |
| Keine Beiträge | Normal, wenn nichts Wichtiges passiert. `newsbot status` zeigt, wie viele Meldungen geprüft wurden |
