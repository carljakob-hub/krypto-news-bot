# Telegram-Bot erstellen und einrichten

## 1. Bot anlegen

1. In Telegram **@BotFather** öffnen (auf den blauen Verifizierungshaken achten).
2. `/newbot` senden, Anzeigenamen und Benutzernamen (muss auf `bot` enden) wählen.
3. BotFather antwortet mit dem **Token** (`123456789:AA…`). Dieses Token ist ein Passwort: nur in `.env` eintragen, nie weitergeben oder committen.
4. Empfohlen bei BotFather:
   - `/setjoingroups` → nach dem Einrichten auf **Disable**, damit niemand den Bot in fremde Gruppen holt.
   - `/setprivacy` → **Enable** (der Bot muss keine Gruppennachrichten lesen).

## 2. Gruppen anlegen

Lege **zwei** Gruppen an:

- eine **private Testgruppe** (nur du), für `TELEGRAM_TEST_CHAT_ID`
- die **Zielgruppe** für den Produktivbetrieb, für `TELEGRAM_CHAT_ID`

Auch ein Kanal ist als Ziel möglich; dort muss der Bot Administrator mit dem Recht „Nachrichten senden“ sein.

## 3. Bot hinzufügen und Rechte setzen

1. Gruppe öffnen → Mitglieder hinzufügen → nach dem Bot-Benutzernamen suchen.
2. Den Bot zum **Administrator** machen und nur das Recht **„Nachrichten senden“** (bzw. bei Kanälen „Nachrichten veröffentlichen“) aktiv lassen. Alle anderen Rechte (Mitglieder verwalten, Nachrichten löschen …) ausschalten.

## 4. Gruppen-ID ermitteln

Variante A (ohne Fremdbots):
1. In der Gruppe eine beliebige Nachricht senden, z. B. `/start@DeinBotName`.
2. Im Browser aufrufen (Token einsetzen, Adresse nicht teilen):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. In der Antwort steht `"chat":{"id":-100…` – das ist die Gruppen-ID. Supergruppen und Kanäle beginnen mit `-100`.

Hinweis: Wenn bereits ein Webhook gesetzt ist, liefert `getUpdates` nichts. Der Newsbot selbst nutzt keine Webhooks.

Für den **Admin-Chat** (Warnungen) kannst du deine eigene Benutzer-ID nehmen: Schreibe dem Bot privat `/start` und lies die `chat.id` ebenso aus `getUpdates` ab.

## 5. Eintragen und prüfen

```bash
# .env
TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_TEST_CHAT_ID=-100111...
TELEGRAM_CHAT_ID=-100222...
TELEGRAM_ADMIN_CHAT_ID=123456789
```

```bash
newsbot check-telegram --send-test
```

Die Prüfung zeigt für jede ID: Token gültig, Chat erreichbar, Chat-Typ, ob der Bot schreiben darf. `--send-test` schickt eine Testnachricht **nur** an die Test- oder Admin-Gruppe, nie an die Produktionsgruppe.

## Sicherheitsverhalten

- Der Bot sendet ausschließlich an die Chat-IDs aus `.env`: im Testmodus Test- und Admin-Chat, im Produktivmodus Produktions- und Admin-Chat. Jede andere ID wird im Code abgelehnt.
- Test- und Produktionsgruppe dürfen nicht identisch sein.
- Ein ungültiges oder widerrufenes Token (HTTP 401) wird sofort erkannt, nicht endlos wiederholt und als Warnung gemeldet. Neues Token: bei BotFather `/revoke`, dann `.env` aktualisieren und neu starten.
