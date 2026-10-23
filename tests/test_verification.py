"""Kernfunktion: strenge Echtheitsprüfung."""

from __future__ import annotations

import pytest

from newsbot.models import LinkCheck, VerificationStatus as S
from newsbot.verification.verifier import Verifier
from tests.conftest import item


@pytest.fixture
def verifier(settings):
    return Verifier(settings.config)


async def test_primary_source_alone_is_verified(verifier, now):
    it = item("sec_press", "SEC Approves Listing of Spot Solana Exchange-Traded Products",
              url="https://www.sec.gov/newsroom/press-releases/2026-123")
    r = await verifier.verify([it], now)
    assert r.status is S.VERIFIED
    assert r.primary_urls == [it.url]
    assert any(e.kind == "primary_source" for e in r.evidence)


async def test_two_independent_trusted_sources_verify(verifier, now):
    a = item("coindesk", "Kraken to acquire derivatives platform NinjaTrader for $1.5 billion",
             "The crypto exchange Kraken agreed to buy NinjaTrader in a deal valued at $1.5 billion.")
    b = item("theblock", "Kraken agrees to buy NinjaTrader in $1.5 billion deal",
             "Crypto exchange Kraken announced a definitive agreement to acquire futures broker NinjaTrader.")
    r = await verifier.verify([a, b], now)
    assert r.status is S.VERIFIED
    assert r.independent_groups == 2


async def test_single_established_source_is_not_enough(verifier, now):
    a = item("coindesk", "Exchange X lists new token Y", "Exchange X said it will list token Y on Friday.")
    r = await verifier.verify([a], now)
    assert r.status is S.UNCONFIRMED


async def test_fake_news_refuted_by_trusted_source(verifier, now):
    claim = item("bitcoinmagazine", "BlackRock files for XRP ETF", "A filing for an XRP trust was published.")
    denial = item("coindesk", "BlackRock says XRP ETF filing is fake",
                  "BlackRock said the filing circulated online is fake and was not submitted by the firm.")
    r = await verifier.verify([claim, denial], now)
    assert r.status is S.REFUTED


async def test_rumors_only_are_unconfirmed(verifier, now):
    a = item("coindesk", "Binance reportedly in talks to acquire rival exchange", "People familiar with the matter said...")
    b = item("cointelegraph", "Rumor: Binance could be buying a rival exchange?", "Unconfirmed reports suggest...")
    r = await verifier.verify([a, b], now)
    assert r.status is S.UNCONFIRMED
    assert any("Gerücht" in x or "spekulativ" in x.lower() for x in r.reasons)


async def test_outdated_news_is_not_verified(verifier, now):
    a = item("coindesk", "Mt. Gox begins repaying creditors in bitcoin", age_hours=24 * 400)
    b = item("theblock", "Mt. Gox starts creditor repayments in BTC", age_hours=24 * 401)
    r = await verifier.verify([a, b], now)
    assert r.status is S.UNCONFIRMED
    assert "veraltet" in " ".join(r.reasons)


async def test_old_story_recirculated(verifier, now):
    old = item("coindesk", "Tesla buys $1.5 billion in bitcoin", age_hours=24 * 30)
    new1 = item("decrypt", "Tesla buys $1.5 billion in bitcoin", "Tesla disclosed the purchase in a filing.", age_hours=1)
    new2 = item("cointelegraph", "Tesla has bought $1.5 billion of bitcoin", "The company disclosed it.", age_hours=1)
    r = await verifier.verify([old, new1, new2], now)
    assert "old_story_recirculated" in r.flags
    assert r.status is S.UNCONFIRMED


async def test_contradicting_numbers_give_partial(verifier, now):
    a = item("coindesk", "DEX Foo drained of $150 million in exploit", "Hackers drained the protocol.")
    b = item("theblock", "Foo protocol loses $40 million in hack", "An attacker exploited a bug.")
    r = await verifier.verify([a, b], now)
    assert r.status is S.PARTIAL
    assert "conflicting_numbers" in r.flags


async def test_syndicated_copies_count_as_one_source(verifier, now):
    text = ("Crypto lender Acme has halted withdrawals citing extreme market conditions, the company said in a "
            "statement on Thursday, adding that customer funds remain safe and operations will resume soon.")
    a = item("coindesk", "Crypto lender Acme halts withdrawals", text)
    b = item("decrypt", "Crypto lender Acme halts withdrawals", text)
    c = item("cointelegraph", "Crypto lender Acme halts all withdrawals", text)
    r = await verifier.verify([a, b, c], now)
    assert "syndicated" in r.flags
    assert r.independent_groups == 1
    assert r.status is not S.VERIFIED


async def test_derived_reporting_from_same_origin(verifier, now):
    a = item("decrypt", "Fund manager files for Avalanche ETF", "According to Bloomberg, the firm filed an S-1.")
    b = item("cointelegraph", "Avalanche ETF filing submitted", "The firm filed an S-1, according to Bloomberg reporting.")
    r = await verifier.verify([a, b], now)
    assert "derived_reporting" in r.flags
    assert r.status is not S.VERIFIED


async def test_same_owner_group_not_independent(settings, now):
    cfg = settings.config.model_copy(deep=True)
    cfg.source("decrypt").owner_group = "coindesk"
    v = Verifier(cfg)
    a = item("coindesk", "Kraken acquires NinjaTrader for $1.5 billion", "Deal announced today by Kraken.")
    b = item("decrypt", "Kraken buys NinjaTrader in $1.5 billion deal", "Kraken confirmed the agreement on Thursday.")
    r = await v.verify([a, b], now)
    assert r.independent_groups == 1
    assert r.status is not S.VERIFIED


async def test_social_media_never_confirms(settings, now):
    cfg = settings.config.model_copy(deep=True)
    cfg.source("bitcoinmagazine").tier = cfg.source("bitcoinmagazine").tier.SOCIAL
    cfg.source("bitcoinmagazine").trust = 0.9
    v = Verifier(cfg)
    a = item("coindesk", "Exchange Foo hacked for $20 million", "The exchange confirmed the incident.")
    b = item("bitcoinmagazine", "Exchange Foo hacked for $20 million", "Posted on X with screenshot.")
    r = await v.verify([a, b], now)
    assert r.status is S.UNCONFIRMED


async def test_primary_link_is_fetched_and_matched(settings, now):
    async def check(url, _now):
        return LinkCheck(url, True, 200, "Federal Reserve issues FOMC statement: committee decided to lower the target "
                                         "range for the federal funds rate by 25 basis points")

    v = Verifier(settings.config, check)
    a = item("coindesk", "Fed cuts rates by 25 basis points", "The FOMC lowered the federal funds rate.",
             links=["https://www.federalreserve.gov/newsevents/pressreleases/monetary20261021a.htm"])
    r = await v.verify([a], now)
    assert r.status is S.VERIFIED
    assert r.primary_urls == ["https://www.federalreserve.gov/newsevents/pressreleases/monetary20261021a.htm"]


async def test_unreachable_primary_link_does_not_verify(settings, now):
    async def check(url, _now):
        return LinkCheck(url, False, None, "", "Netzwerkfehler")

    v = Verifier(settings.config, check)
    a = item("coindesk", "SEC approves spot XRP ETF", "The SEC approved the filing.",
             links=["https://www.sec.gov/files/rules/sro/2026/xrp-approval.pdf"])
    r = await v.verify([a], now)
    assert r.status is S.UNCONFIRMED
    assert "primary_link_unreachable" in r.flags


async def test_primary_link_with_other_content_is_mismatch(settings, now):
    async def check(url, _now):
        return LinkCheck(url, True, 200, "SEC charges investment adviser with misleading clients about fees")

    v = Verifier(settings.config, check)
    a = item("coindesk", "SEC approves spot XRP ETF", "The SEC approved the filing.",
             links=["https://www.sec.gov/litigation/2026/other.htm"])
    r = await v.verify([a], now)
    assert "primary_link_mismatch" in r.flags
    assert r.status is S.UNCONFIRMED


async def test_number_mismatch_with_primary_document(settings, now):
    async def check(url, _now):
        return LinkCheck(url, True, 200, "Fed cuts federal funds rate by 25 basis points FOMC statement")

    v = Verifier(settings.config, check)
    a = item("coindesk", "Fed cuts rates by 50 basis points", "The FOMC lowered the federal funds rate.",
             links=["https://www.federalreserve.gov/newsevents/pressreleases/monetary.htm"])
    r = await v.verify([a], now)
    assert r.status is S.PARTIAL


async def test_lookalike_domain_flagged(verifier, now):
    a = item("coindesk", "Coinbase announces airdrop for users", "Claim at the link.",
             links=["https://coinbase-airdrop.com/claim"])
    b = item("theblock", "Coinbase users targeted with airdrop", "A site offers an airdrop.")
    r = await verifier.verify([a, b], now)
    assert "lookalike_domain" in r.flags
    assert r.status is not S.VERIFIED


async def test_primary_source_with_wrong_domain_not_trusted(verifier, now):
    a = item("sec_press", "SEC approves Bitcoin ETF", url="https://sec-gov-news.example/approval")
    r = await verifier.verify([a], now)
    assert "primary_url_mismatch" in r.flags
    assert r.status is S.UNCONFIRMED


async def test_missing_dates_not_verified(verifier, now):
    a = item("coindesk", "Kraken acquires NinjaTrader", published=False)
    b = item("theblock", "Kraken buys NinjaTrader", published=False)
    r = await verifier.verify([a, b], now)
    assert r.status is S.UNCONFIRMED
    assert "missing_dates" in r.flags


async def test_retraction_blocks_verification(verifier, now):
    a = item("coindesk", "Correction: Exchange Foo did not halt withdrawals; article retracted", "We retracted the article.")
    b = item("theblock", "Exchange Foo halts withdrawals", "Foo halted withdrawals.")
    r = await verifier.verify([a, b], now)
    assert r.status is S.UNCONFIRMED


async def test_unknown_source_ignored(verifier, now):
    a = item("not_configured", "Bitcoin to $1m tomorrow")
    r = await verifier.verify([a], now)
    assert r.status is S.UNCONFIRMED


def test_outlook_detection():
    from newsbot.analysis.outlook import detect_outlook
    assert detect_outlook("Bitcoin ETFs Bleed $636 Million as October Turns Red Again") == "negativ"
    assert detect_outlook("SEC approves spot Solana ETF, inflows expected") == "positiv"
    assert detect_outlook("SEC drops lawsuit against Coinbase") == "positiv"
    assert detect_outlook("Ethereum developers schedule community call") == "neutral"
