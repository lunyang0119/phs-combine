"""Kiwi term-extraction behaviour: the heart of the migration.

The whole point of moving from the old n-gram heuristic to Kiwi is that casual
conjugated Korean chatter should stop producing index noise, while genuine nouns
(dictionary + userdict proper nouns) survive with particles/verbs stripped.
"""

from __future__ import annotations

from collections import Counter

import mogindex_debug
import mogindex_kiwi


def test_casual_chatter_yields_no_terms(kiwi):
    # Conjunctions/pronouns/adverbs/adjective conjugations only -> nothing to index.
    assert mogindex_kiwi.extract_kiwi_terms(kiwi, "근데 그거 진짜 재밌었어") == Counter()


def test_nouns_survive_particles_and_verbs(kiwi):
    # Robust-invariant style (chosen over exact-equality on purpose): Kiwi's exact
    # segmentation of loanwords can vary between versions, so we assert the two
    # nouns that MUST be indexed are present with count 1, and that the stripped
    # particles/verb never leak into the term set.
    terms = mogindex_kiwi.extract_kiwi_terms(kiwi, "아르딘이 카페테리아에서 봤는데")
    assert terms["아르딘"] == 1  # userdict NNP
    assert terms["카페테리아"] == 1  # built-in dictionary noun
    for dropped in ("이", "에서", "봤", "봤는데", "근데", "그거"):
        assert dropped not in terms


def test_discord_artifacts_are_stripped(kiwi):
    # The mention and the URL must contribute no terms; the real noun still does.
    # If the URL were not stripped, its latin path segments would surface as SL
    # terms ("example", "cafe", "com"), so their absence proves the stripping.
    terms = mogindex_kiwi.extract_kiwi_terms(
        kiwi, "아르딘 <@123456> 봤어 https://example.com/cafe"
    )
    assert "아르딘" in terms
    for artifact in ("example", "cafe", "com", "123456"):
        assert artifact not in terms


def test_prepare_kiwi_terms_search_context_asymmetry(kiwi):
    # search_context (channel/thread name) feeds message_terms but NOT content_terms,
    # so it can't pollute the daily keyword rollup.
    message_terms, content_terms = mogindex_kiwi.prepare_kiwi_terms(
        kiwi, "아르딘이 왔다", "카페테리아"
    )
    assert "아르딘" in content_terms
    assert "카페테리아" not in content_terms
    assert "아르딘" in message_terms
    assert "카페테리아" in message_terms


def test_derive_query_terms_strips_particles_without_kiwi():
    # The bot-side query path is Kiwi-free: it strips particles/endings heuristically.
    assert mogindex_debug.derive_query_terms("아르딘이 카페테리아에서") == [
        "아르딘",
        "카페테리아",
    ]
