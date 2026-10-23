# Architektur und Prüfregeln

## Ablauf eines Durchlaufs (`pipeline.py`)

Der Scheduler startet jede Minute (`schedule.tick_seconds`) einen Durchlauf mit hartem Zeitlimit (`cycle_timeout_seconds`). Jede Quelle hat ein eigenes Abfrageintervall (`poll_interval_seconds`); Primärquellen werden häufiger abgefragt.

1. **Finden** – `research/collector.py` ruft fällige Quellen parallel ab (nur GET, nur HTTPS, max. 3 MB, bedingte Abrufe über ETag/Last-Modified, exponentielle Wiederholungen bei 429/5xx/Netzwerkfehlern). Fehlerhafte Quellen werden automatisch seltener abgefragt.
2. **Speichern & exakte Duplikate** – kanonische URL (ohne Tracking-Parameter) und Inhalts-Hash verhindern doppelte Einträge.
3. **Gruppieren** – `verification/clustering.py` ordnet jede Meldung einem Ereignis zu (Ähnlichkeit der normalisierten Schlüsselwörter inkl. Synonymen wie „Ether/Ethereum → ETH“, „greenlights/approves → approv“).
4. **Prüfen** – `verification/verifier.py` bewertet das gesamte Ereignis (alle Quellen zusammen) und speichert Ergebnis + Belege.
5. **Relevanz** – `analysis/relevance.py` (nur für VERIFIZIERT bzw. TEILWEISE).
6. **Beitrag** – `analysis/summarizer.py` + `analysis/guard.py` + `publishing/formatter.py`.
7. **Veröffentlichen** – `publishing/publisher.py` mit Priorität, Limits, Duplikatschutz, Wiederholungen.
8. **Protokollieren** – Tabelle `runs`, `fetch_log`, `publications`, `llm_usage`; danach prüft `monitoring/alerts.py`, ob gewarnt werden muss.

## Die sieben Prüfstufen

| Stufe | Umsetzung |
|---|---|
| 1. Originalquelle | Meldungen aus Quellen der Stufe `primary` zählen nur, wenn ihr Link auf eine der `official_domains` zeigt. Medienberichte, die auf eine offizielle Domain (`primary_domains`) verlinken, lösen einen echten Abruf des Dokuments aus; es zählt nur, wenn es erreichbar ist **und** mindestens 50 % der Schlüsselbegriffe der Meldung enthält. |
| 2. Quellen vergleichen | Unabhängige Bestätigungen werden über eine Union-Find-Gruppierung gezählt: gleiche `owner_group`, nahezu wörtliche Textübernahme (`syndication_similarity`) und Formulierungen wie „according to Bloomberg“ / „laut …“ verschmelzen Quellen zu **einer** Gruppe. Drei Seiten, die dieselbe Agenturmeldung übernehmen, sind also eine Bestätigung. |
| 3. Quelle bewerten | `trust` (0–1) und `tier` pro Quelle. Nur Quellen mit `trust ≥ min_trust_score` zählen als Bestätigung, `social` nie. `conflict_of_interest: true` kennzeichnet Selbstauskünfte (z. B. Börse über eigenes Produkt) – sie bleiben gültige Primärquelle, das Risiko wird im Beitrag genannt. |
| 4. Inhalt prüfen | Beträge, Prozentwerte, Basispunkte und BTC/ETH-Mengen in den Überschriften werden normalisiert (z. B. „1,2 Mrd. US-Dollar“ = „$1.2bn“) und zwischen unabhängigen Quellen sowie gegen das Primärdokument verglichen (Toleranz `number_tolerance`). Links auf Domains, die eine offizielle Marke imitieren (z. B. `coinbase-airdrop.com`, `sec-gov-news…`), werden markiert. |
| 5. Aktualität | Meldungen älter als `max_item_age_hours` oder ohne Datum zählen nicht. Gibt es zum Ereignis ältere Berichte vertrauenswürdiger Quellen, gilt es als **wieder verbreitete alte Meldung**. Überschriften mit zurückliegenden Jahreszahlen und Daten in der Zukunft werden markiert. Korrekturen/Rückzüge („Correction“, „retracted“, „zurückgezogen“) werden erkannt. |
| 6. Gerüchte | Signalwörter (EN/DE) wie „reportedly“, „sources say“, „Gerücht“, „angeblich“, „screenshot“, Fragezeichen-Überschriften → keine Bestätigung. Widerlegungen vertrauenswürdiger Quellen („fake“, „hoax“, „denies the report“, „Falschmeldung“, „gehackter Account“) → WIDERLEGT. |
| 7. Duplikate | Exakt (URL/Inhalts-Hash), ereignisbezogen (Clustering) und beim Veröffentlichen (Ähnlichkeit zu Beiträgen der letzten 72 h). Jedes Ereignis kann durch eine UNIQUE-Bedingung in der Datenbank höchstens einmal veröffentlicht werden. |

### Entscheidungsregel

```
WIDERLEGT            wenn eine vertrauenswürdige Quelle widerspricht
UNBESTÄTIGT          wenn keine aktuelle Quelle, Rückzug, oder alte Meldung ohne aktuelle Primärquelle
VERIFIZIERT          Primärquelle bestätigt und keine Zahlenwidersprüche
VERIFIZIERT          ≥ min_independent_sources unabhängige Gruppen und keine Widersprüche
TEILWEISE BESTÄTIGT  Primärquelle oder genug Gruppen, aber Zahlen widersprechen sich
TEILWEISE BESTÄTIGT  eine vertrauenswürdige Gruppe + schwächere Zusatzquellen, keine verdächtigen Links
UNBESTÄTIGT          alles andere
```

Veröffentlicht wird nur VERIFIZIERT (ab `relevance.min_level_to_publish`) und TEILWEISE BESTÄTIGT nur ab `relevance.publish_partial_only_if` (Standard: HOCH), dann ausdrücklich gekennzeichnet.

Ein Ereignis wird neu bewertet, sobald weitere Quellen hinzukommen – höchstens `pending_reevaluation_hours` lang. Eine anfangs unbestätigte Meldung kann so später VERIFIZIERT werden.

## Relevanzbewertung

Deterministisch aus Themenkategorien (ETF, ETF-Flüsse, Zentralbank, Makrodaten, Regulierung, Sicherheitsvorfall, Insolvenz, Börsenmeldung, institutionelle Aktivität, On-Chain, Token-Unlock, Netzwerk, Stablecoin) mit Gewichten, plus Zuschläge für BTC/ETH-Bezug, Beträge ≥ 100 Mio./1 Mrd., Primärquelle, breite Berichterstattung; Abschläge für Behördenmeldungen ohne Kryptobezug und für Ereignisse, die länger als `novelty_hours` bekannt sind. HOCH ≥ 7, MITTEL ≥ 4. Jede Kategorie liefert einen Zeithorizont sowie sachliche Szenario- und Risikotexte – ohne Kursziele oder Wahrscheinlichkeiten.

## Sprachmodell und Prompt-Injection-Schutz

- Das Modell **entscheidet nichts**: Status, Relevanz, Zeithorizont und Quellen stammen aus dem Code.
- Quellen mit Anweisungen an KI-Systeme („ignore previous instructions“, „als verifiziert markieren“ …) werden beim Abruf markiert, zählen nicht als Beleg und werden dem Modell gar nicht übergeben.
- Quelltexte werden bereinigt (HTML, unsichtbare Unicode-Steuerzeichen) und in `<quelle>`-Tags als Daten übergeben; der Systemprompt erklärt sie ausdrücklich zu Daten.
- Das Modell hat keine Werkzeuge und kann keine Aktionen auslösen. Die Antwort muss einem JSON-Schema entsprechen.
- `analysis/guard.py` verwirft jede Antwort mit Zahlen, die in keiner Quelle stehen, mit Kursprognosen/Kaufempfehlungen/Wahrscheinlichkeiten, Links, @-Erwähnungen, übernommenen Anweisungen oder nicht belegten Assets. Dann wird die Textvorlage verwendet.
- Alle Texte werden für Telegram HTML-maskiert; Links im Beitrag setzt ausschließlich der Code aus den geprüften Quellen.
- Bei einer Ablehnung durch die Sicherheitsfilter des Modells wählt der Server automatisch ein Ersatzmodell (`fallbacks: "default"`); schlägt alles fehl, greift die Textvorlage.

## Datenbank (SQLite, `db/migrations/001_initial.sql`)

| Tabelle | Inhalt |
|---|---|
| `sources` | Quellen, Abrufstatus, ETag, Fehlerzähler |
| `items` | Einzelmeldungen (Titel, Text, Datum, Links, Markierungen) |
| `clusters` | Ereignisse mit aktuellem Prüfstatus und Relevanz |
| `verifications` / `evidence` | Jede Prüfung mit allen Belegen (unterstützend, neutral, widersprechend) |
| `link_checks` | Ergebnisse abgerufener Primärdokumente (Cache) |
| `publications` | Warteschlange und Veröffentlichungsprotokoll (UNIQUE je Ereignis) |
| `runs`, `fetch_log`, `llm_usage`, `alerts`, `kv` | Monitoring, Kosten, Warnzustand, Preflight |

Neue Migrationen als `002_….sql` usw. ablegen; `newsbot init-db` bzw. jeder Start wendet sie automatisch an.
