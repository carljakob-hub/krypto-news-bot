"""Deterministische Bewertung der Trading-Relevanz eines verifizierten Ereignisses."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from newsbot.config import AppConfig, Tier
from newsbot.models import RawItem, RelevanceLevel, RelevanceResult, VerificationResult, VerificationStatus
from newsbot.textutils import extract_numbers

ASSETS: dict[str, list[str]] = {
    "BTC": [r"\bbitcoin", r"\bbtc\b"],
    "ETH": [r"\bethereum\b", r"\bether\b", r"\beth\b"],
    "SOL": [r"\bsolana\b", r"\bsol\b"],
    "XRP": [r"\bxrp\b", r"\bripple\b"],
    "BNB": [r"\bbnb\b"],
    "ADA": [r"\bcardano\b", r"\bada\b"],
    "DOGE": [r"\bdogecoin\b", r"\bdoge\b"],
    "USDT": [r"\btether\b", r"\busdt\b"],
    "USDC": [r"\busdc\b"],
    "LINK": [r"\bchainlink\b"],
    "AVAX": [r"\bavalanche\b", r"\bavax\b"],
    "TON": [r"\btoncoin\b"],
    "LTC": [r"\blitecoin\b", r"\bltc\b"],
}

_CRYPTO = re.compile(r"crypto|krypto|digital asset|digitale? (vermögens|asset)|stablecoin|blockchain|token|defi|web3|\bmica\b|"
                     + "|".join(p for ps in ASSETS.values() for p in ps), re.I)


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    weight: float
    patterns: tuple[str, ...]
    horizon: str
    needs_crypto: bool
    impact: str
    risk: str
    reactions: tuple[str, ...] = ()


CATEGORIES: list[Category] = [
    Category("etf", "ETF", 4.0,
             (r"\betfs?\b", r"exchange-traded", r"spot (bitcoin|ether|ethereum|solana|xrp)", r"\b(19b-4|s-1)\b"),
             "Kurz- bis mittelfristig", True,
             "ETF-Entscheidungen und -Flüsse beeinflussen die institutionelle Nachfrage; Auswirkungen auf Liquidität und Stimmung sind plausibel, ihre Stärke ist offen.",
             "Ob und wann Mittelflüsse tatsächlich einsetzen, ist ungewiss; ein Teil der Erwartung kann bereits eingepreist sein.",
             ('Positiv: Zulassung oder neue Produkte könnten zusätzliche Nachfrage bringen und den Kurs des betroffenen Assets stützen.', 'Negativ: Ablehnung, Verzögerung oder „Sell the News“ nach hohen Erwartungen könnten Kursrücksetzer auslösen.', 'Worauf achten: tägliche ETF-Zuflüsse, Handelsvolumen am ersten Handelstag, Reaktion verwandter Altcoins.',)),
    Category("etf_flows", "ETF-Flüsse", 3.0,
             (r"\b(inflows?|outflows?|zuflüsse|abflüsse|net flows?)\b",),
             "Kurzfristig", True,
             "Deutliche ETF-Zu- oder Abflüsse können kurzfristig auf Nachfrage und Stimmung hindeuten.",
             "Tageswerte schwanken stark und werden teils nachträglich korrigiert.",
             ('Positiv: anhaltende Zuflüsse sprechen für steigende institutionelle Nachfrage und könnten Aufwärtsdruck erzeugen.', 'Negativ: anhaltende Abflüsse können auf Gewinnmitnahmen hindeuten und Abwärtsdruck erzeugen.', 'Worauf achten: ob sich der Trend über mehrere Tage fortsetzt und wie sich der Spot-Preis dazu verhält.',)),
    Category("central_bank", "Zentralbank/Zinsen", 4.0,
             (r"\b(fomc|federal reserve|federal funds|interest rate|rate (cut|hike|decision)|leitzins|zinsentscheid|"
              r"monetary policy|geldpolitik|ecb|ezb|bank of japan|powell|lagarde)\b",),
             "Kurz- bis mittelfristig", False,
             "Zinsentscheidungen und Aussagen von Zentralbanken verändern die Liquiditätserwartungen und betreffen Risikoanlagen einschließlich Kryptowährungen.",
             "Die Marktreaktion hängt stark von den Erwartungen vor der Entscheidung und vom weiteren Ausblick ab.",
             ('Lockere Geldpolitik (Zinssenkung, dovishe Aussagen): könnte Risikoanlagen wie BTC und ETH stützen.', 'Straffere Geldpolitik (Zinserhöhung, hawkishe Aussagen): könnte Druck auf Krypto und Aktien ausüben.', 'Worauf achten: Pressekonferenz und Ausblick, US-Dollar, Anleiherenditen, Reaktion des Nasdaq.',)),
    Category("macro", "Makrodaten", 3.0,
             (r"\b(cpi|consumer price|inflation|verbraucherpreis|nonfarm|payrolls?|employment situation|unemployment|"
              r"arbeitsmarkt|arbeitslosen|gdp|bip|pce|jobs report)\b",),
             "Kurzfristig", False,
             "Makrodaten können die Zinserwartungen verschieben und so kurzfristig Volatilität in Risikoanlagen auslösen.",
             "Einzelne Datenpunkte werden häufig revidiert; die Reaktion hängt vom Vergleich mit den Prognosen ab.",
             ('Daten schwächer als erwartet (z. B. niedrigere Inflation): Hoffnung auf Zinssenkungen könnte Krypto stützen.', 'Daten stärker als erwartet (z. B. höhere Inflation): Zinssorgen könnten Verkaufsdruck auslösen.', 'Worauf achten: Abweichung von der Prognose, kurzfristige Volatilität direkt nach Veröffentlichung, US-Dollar.',)),
    Category("regulation", "Regulierung", 3.5,
             (r"\b(regulat|regulier|lawsuit|klage|enforcement|settle|charges?|sues?|ban\b|verbot|bill\b|gesetz|act\b|"
              r"mica|license|lizenz|approv|genehmig|sanction|sanktion|court\b|gericht|ruling|urteil)",),
             "Mittel- bis langfristig", True,
             "Regulatorische Entscheidungen können den Marktzugang, die Rechtssicherheit und die Bewertung betroffener Projekte verändern.",
             "Rechtsverfahren und Gesetzgebung ziehen sich oft hin; Einsprüche und Umsetzungsdetails sind offen.",
             ('Positiv: mehr Rechtssicherheit oder Genehmigungen könnten das Vertrauen stärken und betroffene Token stützen.', 'Negativ: Klagen, Verbote oder strenge Auflagen könnten Verkäufe und Delistings betroffener Token auslösen.', 'Worauf achten: Reaktionen der betroffenen Unternehmen, Fristen, mögliche Einsprüche.',)),
    Category("security", "Sicherheitsvorfall", 4.5,
             (r"\b(hack|hacked|exploit|drained|breach|stolen|gehackt|gestohlen|sicherheitslücke|vulnerability|attack)",),
             "Kurzfristig", True,
             "Sicherheitsvorfälle können kurzfristig Verkaufsdruck bei betroffenen Token und Misstrauen gegenüber der Plattform auslösen.",
             "Schadenshöhe und Ursache werden in den ersten Stunden oft korrigiert; Rückerstattungen sind unklar.",
             ('Wahrscheinlicher Druck: betroffene Token und die Plattform selbst könnten Verkaufsdruck und Abflüsse sehen.', 'Entspannung möglich: wenn Verluste gedeckt oder Gelder eingefroren werden, kann sich der Kurs erholen.', 'Worauf achten: Bewegungen der Hacker-Wallets, Stellungnahme des Teams, Ein- und Auszahlungsstopps.',)),
    Category("insolvency", "Insolvenz/Auszahlungsstopp", 5.0,
             (r"\b(bankrupt|insolven|chapter 11|halts? withdrawals|suspends? withdrawals|auszahlungsstopp|liquidation|"
              r"zahlungsunfähig)",),
             "Kurz- bis mittelfristig", True,
             "Insolvenzen oder gestoppte Auszahlungen können Ansteckungseffekte und erhöhte Volatilität auslösen.",
             "Das tatsächliche Ausmaß und betroffene Gegenparteien sind häufig zunächst unbekannt.",
             ('Ansteckungsgefahr: betroffene Token, Partner und Gegenparteien könnten deutlich unter Druck geraten.', 'Marktweite Folgen: erhöhte Volatilität und Liquidationen bei gehebelten Positionen sind möglich.', 'Worauf achten: Abflüsse bei anderen Börsen/Lendern, offizielle Gerichtsdokumente, Stablecoin-Kurse.',)),
    Category("exchange", "Börsenmeldung", 2.5,
             (r"\b(listing|lists|delist|delisting|listet|futures|perpetual|outage|ausfall|maintenance)",),
             "Kurzfristig", True,
             "Listings, Delistings oder Ausfälle großer Handelsplätze können kurzfristig Liquidität und Preise einzelner Token beeinflussen.",
             "Die Wirkung von Listings ist oft kurzlebig und kann bereits vorweggenommen sein.",
             ('Listing: kurzfristige Kurssprünge beim betroffenen Token sind häufig, oft aber nicht dauerhaft.', 'Delisting oder Ausfall: Verkaufsdruck und eingeschränkte Liquidität beim betroffenen Token möglich.', 'Worauf achten: genaue Startzeit, Handelspaare, Volumen in den ersten Stunden.',)),
    Category("institutional", "Institutionelle Aktivität", 3.0,
             (r"\b(acquir|acquisition|übernahme|merger|invests?|investment|raises|funding round|treasury|purchases?|buys|"
              r"kauft|erwirbt|allocat)",),
             "Mittelfristig", True,
             "Größere institutionelle Käufe oder Übernahmen können das Vertrauen stärken oder Marktanteile verschieben.",
             "Einzelne Transaktionen sagen wenig über die künftige Nachfrage aus.",
             ('Positiv: größere Käufe oder Investitionen könnten das Vertrauen und die Nachfrage stärken.', 'Negativ: Verkäufe oder geplatzte Deals könnten die Stimmung belasten.', 'Worauf achten: Größe im Verhältnis zum Tagesvolumen, ob weitere Institutionen folgen.',)),
    Category("onchain", "On-Chain/Großtransfers", 2.5,
             (r"\b(whale|on-chain|onchain|transfer(red)?|moved|wallet|outflows? from|inflows? to|large transaction)",),
             "Kurzfristig", True,
             "Große Transfers zu oder von Börsen werden häufig als mögliches Verkaufs- oder Kaufsignal gelesen, ohne dass die Absicht bekannt ist.",
             "Die Zuordnung von Wallets ist oft unsicher; interne Umbuchungen werden leicht fehlgedeutet.",
             ('Transfers zu Börsen: werden oft als mögliches Verkaufssignal gelesen und können Druck erzeugen.', 'Transfers von Börsen weg: werden oft als Halten/Akkumulation gelesen.', 'Worauf achten: Wem gehört die Wallet wirklich? Folgen tatsächlich Verkäufe?',)),
    Category("token_unlock", "Token-Unlock", 3.0,
             (r"\b(unlock|vesting|token release|freischaltung)",),
             "Kurzfristig", True,
             "Token-Unlocks erhöhen das handelbare Angebot und können Verkaufsdruck auslösen.",
             "Ob Empfänger tatsächlich verkaufen, ist offen; Termine sind meist vorab bekannt.",
             ('Negativ: mehr handelbares Angebot könnte vor und nach dem Termin Verkaufsdruck erzeugen.', 'Neutral bis positiv: ist der Unlock schon eingepreist oder halten die Empfänger, bleibt die Wirkung gering.', 'Worauf achten: Größe im Verhältnis zum Umlauf, Transfers der Empfänger zu Börsen.',)),
    Category("network", "Netzwerkereignis", 3.0,
             (r"\b(upgrade|hard fork|fork|mainnet|testnet|halving|outage|chain halt|consensus|eip-\d+|client release)",),
             "Mittelfristig", True,
             "Netzwerk-Upgrades oder Störungen betreffen Funktionalität und Wahrnehmung des Netzwerks.",
             "Termine verschieben sich häufig; technische Risiken bei der Umsetzung bleiben.",
             ('Positiv: erfolgreiche Upgrades könnten Vertrauen und Nutzung des Netzwerks stärken.', 'Negativ: Störungen, Verzögerungen oder Fehler könnten Verkaufsdruck auslösen.', 'Worauf achten: offizieller Zeitplan, Status des Netzwerks, Ein-/Auszahlungen bei Börsen.',)),
    Category("stablecoin", "Stablecoin", 3.0,
             (r"\b(depeg|de-peg|stablecoin|reserves?|mint(ed)?|burn(ed)?)\b",),
             "Kurzfristig", True,
             "Veränderungen bei Stablecoins betreffen die Liquidität am gesamten Kryptomarkt.",
             "Angaben zu Reserven und Ausgaben sind ohne Prüfbericht nur eingeschränkt belastbar.",
             ('Stabil: neue Ausgaben (Mint) werden oft als frisches Kapital für den Markt gelesen.', 'Risiko: Abweichung vom Dollar-Kurs (Depeg) könnte Panik und marktweite Verkäufe auslösen.', 'Worauf achten: Kurs nahe 1 $, Rücknahmen, Stellungnahmen des Emittenten.',)),
]

_LEVEL_THRESHOLDS = {"HOCH": 7.0, "MITTEL": 4.0}


class RelevanceAnalyzer:
    def __init__(self, config: AppConfig):
        self.cfg = config

    def detect_assets(self, text: str) -> list[str]:
        low = text.lower()
        return [a for a, pats in ASSETS.items() if any(re.search(p, low) for p in pats)]

    def categories(self, text: str) -> list[Category]:
        low = text.lower()
        crypto = bool(_CRYPTO.search(low))
        out = []
        for c in CATEGORIES:
            if c.needs_crypto and not crypto:
                continue
            if any(re.search(p, low) for p in c.patterns):
                out.append(c)
        return out

    def analyze(self, items: list[RawItem], verification: VerificationResult, first_seen: datetime, now: datetime) -> RelevanceResult:
        text = " ".join(f"{i.title}. {i.summary}" for i in items)
        assets = self.detect_assets(text)
        cats = self.categories(text)
        reasons: list[str] = []
        if not cats:
            return RelevanceResult(RelevanceLevel.LOW, 0.0, assets, [], "Unklar", ["Kein trading-relevantes Thema erkannt"], "unbekannt")

        cats.sort(key=lambda c: -c.weight)
        score = cats[0].weight + 0.5 * min(len(cats) - 1, 2)
        reasons.append(f"Thema: {', '.join(c.label for c in cats[:3])}")

        if "BTC" in assets or "ETH" in assets:
            score += 1.0
            reasons.append("Betrifft BTC/ETH (hohe Marktkapitalisierung)")
        elif assets:
            score += 0.5

        big = max((n.value for i in items for n in extract_numbers(i.title) if n.unit in {"usd", "eur"}), default=0)
        if big >= 1e9:
            score += 1.5
            reasons.append("Betrag in Milliardenhöhe")
        elif big >= 1e8:
            score += 1.0
            reasons.append("Betrag über 100 Mio.")

        if verification.status is VerificationStatus.VERIFIED and verification.primary_urls:
            score += 0.5
        if verification.independent_groups >= 3:
            score += 0.5
            reasons.append("Breit berichtet")

        if any(self.cfg.source(i.source_id) and self.cfg.source(i.source_id).tier is Tier.PRIMARY for i in items):
            if not any(c.key in {"central_bank", "macro"} for c in cats) and not _CRYPTO.search(text.lower()):
                score -= 3.0  # z. B. Behördenmeldung ohne Kryptobezug
                reasons.append("Behördenmeldung ohne direkten Kryptobezug")

        age = now - first_seen
        if age > timedelta(hours=self.cfg.relevance.novelty_hours):
            score -= 2.0
            novelty = "bereits länger bekannt"
            reasons.append("Bereits seit mehreren Stunden bekannt (evtl. eingepreist)")
        else:
            novelty = "neu"

        level = (
            RelevanceLevel.HIGH if score >= _LEVEL_THRESHOLDS["HOCH"]
            else RelevanceLevel.MEDIUM if score >= self.cfg.relevance.medium_min_score
            else RelevanceLevel.LOW
        )
        return RelevanceResult(level, round(score, 2), assets, [c.key for c in cats], cats[0].horizon, reasons, novelty)

    @staticmethod
    def category(key: str) -> Category | None:
        return next((c for c in CATEGORIES if c.key == key), None)
