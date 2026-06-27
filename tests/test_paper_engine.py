"""Tests for paper scoring engine, profile system, and ranking.

Covers:
- Unicode NFKC normalization, lightweight stemmer, bigram matching
- Term rarity weighting, query intent classifier
- Profile registry, lookup, scoring, ranking, venue prestige
- Field-aware citation divisor, configurable scoring weights
- Agent-skill backward compatibility
- Performance benchmarks
"""

from __future__ import annotations

import time

from paper_search_mcp.engine.paper import (
    # ── Scoring / text processing ──────────────────────────────────
    _normalize_lookup_text,
    _stem_word,
    _stemmed_tokens,
    _query_term_match_ratio,
    _query_terms,
    _query_bigrams,
    _classify_query_intent,
    _paper_score,
    _profile_score,
    _rank_papers_for_profile,
    # ── Profile system ─────────────────────────────────────────────
    get_profile,
    list_profiles,
    AGENT_SKILL_RANKING_PROFILE,
    AGENT_SKILL_PROFILE_ALIASES,
    # ── Scoring config ─────────────────────────────────────────────
    SCORING_WEIGHTS,
    _venue_prestige_score,
    _field_aware_citation_divisor,
    _query_category_match,
)


# ===================================================================
# Section 1: Text Processing & Scoring Engine
# ===================================================================

def test_unicode_normalization():
    """NFKC normalizes full-width letters and ligatures."""
    assert _normalize_lookup_text("ＡＢＣ") == "abc"
    assert "file" in _normalize_lookup_text("first file")
    result = _normalize_lookup_text("Café")
    assert "caf" in result  # NFKC preserves accents


def test_stemmer():
    """Lightweight stemmer reduces common suffixes (approximate)."""
    # The stemmer is approximate; only assert on well-established patterns
    assert _stem_word("learning") == "learn"
    assert _stem_word("learned") == "learn"
    assert _stem_word("transformers") == "transformer"
    assert _stem_word("running") == "runn"
    # "-tion" and "-ion" suffixes are handled heuristically
    assert "detect" in _stem_word("detection")  # detect…ion


def test_stemmed_tokens():
    """Stemmed tokens keep both original and stemmed forms."""
    tokens = _stemmed_tokens("deep learning models for image segmentation")
    assert "learn" in tokens
    assert "learning" in tokens
    assert "model" in tokens
    assert "deep" in tokens


def test_bigram_matching():
    """Full bigram match scores higher than partial."""
    terms = _query_terms("deep learning image segmentation")
    bigrams = _query_bigrams(terms)
    assert "deep learning" in bigrams
    assert "learning image" in bigrams
    assert "image segmentation" in bigrams

    text_full = "deep learning for image segmentation with cnns"
    text_partial = "deep neural networks for image segmentation"
    assert _query_term_match_ratio(terms, text_full) > _query_term_match_ratio(terms, text_partial)


def test_rarity_weighting():
    """Rare terms boost match ratio above baseline."""
    rare_terms = ["nerf", "gaussian", "splatting"]
    rare_score = _query_term_match_ratio(rare_terms, "nerf gaussian splatting for 3d rendering")
    assert rare_score > 0.85


def test_query_intent_classifier():
    """Query intent classifier returns domain labels."""
    queries = [
        "image segmentation with vision transformers",
        "side-channel attacks on AES encryption",
        "software testing with fuzzing and symbolic execution",
    ]
    for q in queries:
        intents = _classify_query_intent(q, top_k=2)
        assert len(intents) >= 1
        for name, _conf in intents:
            assert isinstance(name, str) and len(name) > 0


def test_paper_score_with_stems():
    """'learning' query matches 'learned' in abstract via stemmer."""
    paper = {
        "title": "Optimization Methods",
        "abstract": "We investigated several learned optimization methods for deep networks",
        "source": "arxiv",
        "doi": "10.1234/test",
    }
    score = _paper_score(paper, query="learning optimization methods")
    assert score > 2.5, f"Expected > 2.5, got {score}"


# ===================================================================
# Section 2: Profile System
# ===================================================================

def test_profile_registry():
    """At least 10 built-in profiles are registered."""
    profiles = list_profiles()
    assert len(profiles) >= 10, f"Expected >=10 profiles, got {len(profiles)}"


def test_profile_lookup():
    """Profile lookup works with hyphens and underscores."""
    assert get_profile("agent-skill") is not None
    assert get_profile("agent_skill") is not None
    assert get_profile("cv") is not None
    assert get_profile("computer-vision") is not None
    assert get_profile("nlp") is not None
    assert get_profile("ml") is not None
    assert get_profile("security") is not None
    assert get_profile("systems") is not None
    assert get_profile("se") is not None
    assert get_profile("robotics") is not None
    assert get_profile("hci") is not None
    assert get_profile("theory") is not None
    assert get_profile("graphics") is not None
    assert get_profile("nonexistent") is None


def test_query_terms_filtering():
    """Stop words are removed from query terms."""
    terms = _query_terms("deep learning for medical image segmentation")
    assert "deep" in terms
    assert "for" not in terms
    assert "the" not in terms
    assert "segmentation" in terms


def test_term_match_ratio():
    """Match ratio: full >= 1.0, partial >= 0.45, no match = 0."""
    terms = ["deep", "learning", "image", "segmentation"]
    assert _query_term_match_ratio(terms, "deep learning for image segmentation") >= 1.0
    assert _query_term_match_ratio(terms, "deep learning with transformers") >= 0.45
    assert _query_term_match_ratio(terms, "chemical synthesis of polymers") == 0.0


def test_category_match():
    """cs.CV paper matches 'computer vision' query, q-bio does not."""
    assert _query_category_match("computer vision image recognition", {"categories": "cs.CV"}) > 0
    assert _query_category_match("language model nlp", {"categories": "cs.CL"}) > 0
    assert _query_category_match("computer vision", {"categories": "q-bio.GN"}) == 0.0


def test_field_aware_citations():
    """High-citation fields (AI) get larger divisor than lower (Theory)."""
    div_ai = _field_aware_citation_divisor({"categories": "cs.AI"})
    div_theory = _field_aware_citation_divisor({"categories": "cs.DS"})
    assert div_ai > div_theory, f"AI divisor {div_ai} should > theory {div_theory}"
    assert _field_aware_citation_divisor({}) > 0


def test_venue_prestige():
    """Well-known venues get prestige boost; unknown get zero."""
    assert _venue_prestige_score({"journal": "Nature"}) > 0
    assert _venue_prestige_score({"venue": "CVPR"}) > 0
    assert _venue_prestige_score({"venue": "osdi"}) > 0
    assert _venue_prestige_score({"venue": "Unknown Workshop 2025"}) == 0.0


def test_profile_scoring():
    """Domain papers score high on matching profile, low on unrelated."""
    cv_spec = get_profile("cv")
    cv_score = _profile_score({
        "title": "Image Segmentation with Vision Transformers",
        "abstract": "novel method for image segmentation using vision transformers",
        "keywords": "image segmentation, computer vision, deep learning",
        "categories": "cs.CV",
    }, cv_spec)
    assert cv_score > 5, f"CV paper should score >5, got {cv_score}"

    nlp_spec = get_profile("nlp")
    nlp_score = _profile_score({
        "title": "BERT: Pre-training of Deep Bidirectional Transformers",
        "abstract": "language representation with transformers for NLP tasks",
        "keywords": "language model, transformer, NLP",
        "categories": "cs.CL",
    }, nlp_spec)
    assert nlp_score > 5, f"NLP paper should score >5, got {nlp_score}"

    sec_spec = get_profile("security")
    sec_score = _profile_score({
        "title": "Differential Privacy for Federated Learning",
        "abstract": "protecting privacy in federated learning with differential privacy",
        "keywords": "privacy, security, federated learning",
        "categories": "cs.CR",
    }, sec_spec)
    assert sec_score > 3, f"Security paper should score >3, got {sec_score}"

    # Unrelated paper scores low on every profile
    unrelated = {
        "title": "Chemical Synthesis of Novel Reagents",
        "abstract": "new method for chemical synthesis of reagents",
        "keywords": "chemistry, synthesis",
        "categories": "q-bio",
    }
    for name in ("cv", "nlp", "ml", "security", "systems", "robotics", "hci", "theory", "graphics"):
        s = _profile_score(unrelated, get_profile(name))
        assert s <= 0.5, f"Unrelated on {name}: {s} > 0.5"


def test_enhanced_paper_score():
    """Paper with abstract scores higher than paper without."""
    paper_with = {
        "title": "A Novel Approach",
        "abstract": "deep learning for image recognition and computer vision tasks with cnns",
        "doi": "10.1234/test", "pdf_url": "https://arxiv.org/pdf/1234.5678.pdf",
        "source": "arxiv",
    }
    paper_without = {"title": "A Novel Approach", "doi": "10.1234/test", "source": "arxiv"}
    assert _paper_score(paper_with, query="deep learning image recognition") > \
           _paper_score(paper_without, query="deep learning image recognition")


def test_ranking_profiles():
    """Each profile ranks its domain paper first."""
    papers = [
        {"title": "ImageNet Classification with Deep CNNs",
         "abstract": "deep cnn for image classification on imagenet dataset",
         "categories": "cs.CV", "source": "arxiv"},
        {"title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
         "abstract": "language model pre-training with transformers",
         "categories": "cs.CL", "source": "arxiv"},
        {"title": "Differential Privacy for Federated Learning Systems",
         "abstract": "privacy-preserving federated learning",
         "categories": "cs.CR", "source": "arxiv"},
        {"title": "Chemical Properties of Novel Polymers",
         "abstract": "polymer chemistry and synthesis",
         "source": "crossref"},
    ]

    ranked_cv = _rank_papers_for_profile(papers, ranking_profile="cv")
    assert "ImageNet" in ranked_cv[0]["title"]

    ranked_nlp = _rank_papers_for_profile(papers, ranking_profile="nlp")
    assert "BERT" in ranked_nlp[0]["title"]

    ranked_sec = _rank_papers_for_profile(papers, ranking_profile="security")
    assert "Privacy" in ranked_sec[0]["title"]

    ranked_unknown = _rank_papers_for_profile(papers, ranking_profile="unknown")
    assert len(ranked_unknown) == len(papers)


def test_agent_skill_backward_compat():
    """Agent-skill profile aliases are all recognized."""
    assert AGENT_SKILL_RANKING_PROFILE == "agent-skill"
    assert "agent-skill" in AGENT_SKILL_PROFILE_ALIASES
    assert "agent_skill" in AGENT_SKILL_PROFILE_ALIASES

    spec = get_profile("agent-skill")
    agent_paper = {
        "title": "LLM Agent Skill Library: A Benchmark for Tool-Using Agents",
        "abstract": "benchmark for evaluating agent skills with llm agents and tool use",
        "source": "arxiv",
    }
    assert _profile_score(agent_paper, spec) > 10

    reagent_paper = {
        "title": "New reagent for protein analysis",
        "abstract": "using reagent in biochemical assays",
        "source": "crossref",
    }
    assert _profile_score(reagent_paper, spec) <= 0.5


def test_scoring_weights_config():
    """All expected scoring weight keys are present."""
    for key in ("title_term_match", "top_venue", "major_venue",
                "profile_boost_phrase", "citation_divisor"):
        assert key in SCORING_WEIGHTS, f"Missing weight key: {key}"


# ===================================================================
# Section 3: Performance Benchmarks
# ===================================================================

def benchmark():
    """Micro-benchmarks for scoring pipeline throughput."""
    iterations = 5000
    sample_text = (
        "deep learning for image segmentation using convolutional neural networks "
        "with attention mechanisms and transformer architectures for computer vision "
        "tasks including object detection and semantic segmentation"
    )

    # Unicode normalization
    start = time.perf_counter()
    for _ in range(iterations):
        _normalize_lookup_text(sample_text)
    unicode_us = (time.perf_counter() - start) / iterations * 1_000_000
    print(f"  _normalize_lookup_text: {unicode_us:.1f} μs/call")

    # Stemmer (per word)
    words = ["learning", "segmentation", "convolutional", "transformers",
             "detection", "optimization", "classification", "running"]
    start = time.perf_counter()
    for _ in range(iterations):
        for w in words:
            _stem_word(w)
    stem_us = (time.perf_counter() - start) / (iterations * len(words)) * 1_000_000
    print(f"  _stem_word (per word): {stem_us:.1f} μs")

    # Query term match ratio
    terms = _query_terms("deep learning image segmentation cnn transformer")
    start = time.perf_counter()
    for _ in range(iterations):
        _query_term_match_ratio(terms, sample_text)
    match_us = (time.perf_counter() - start) / iterations * 1_000_000
    print(f"  _query_term_match_ratio: {match_us:.1f} μs/call")

    # Query intent classifier
    queries = [
        "image segmentation with vision transformers",
        "language model pre-training",
        "side-channel attacks on AES",
        "software testing fuzzing",
    ]
    start = time.perf_counter()
    for _ in range(iterations):
        for q in queries:
            _classify_query_intent(q, top_k=2)
    class_us = (time.perf_counter() - start) / (iterations * len(queries)) * 1_000_000
    print(f"  _classify_query_intent: {class_us:.1f} μs/call")

    # _paper_score full call
    paper = {
        "title": "Deep Learning for Image Segmentation",
        "abstract": sample_text,
        "keywords": "deep learning, image segmentation, cnn",
        "source": "arxiv",
        "doi": "10.1234/test",
    }
    start = time.perf_counter()
    for _ in range(iterations):
        _paper_score(paper, query="deep learning image segmentation")
    score_us = (time.perf_counter() - start) / iterations * 1_000_000
    print(f"  _paper_score (full): {score_us:.1f} μs/call")

    # Profile scoring
    cv_spec = get_profile("cv")
    start = time.perf_counter()
    for _ in range(iterations):
        _profile_score(paper, cv_spec)
    profile_us = (time.perf_counter() - start) / iterations * 1_000_000
    print(f"  _profile_score (cv): {profile_us:.1f} μs/call")

    total = unicode_us + match_us + score_us + profile_us
    print(f"\n  Total per-paper overhead: ~{total:.0f} μs = {total / 1000:.2f} ms")
    print(f"  For 100 papers: ~{total * 100 / 1000:.2f} ms")
    print(f"  vs 18s search time: {(total * 100 / 1000) / 18000 * 100:.3f}% overhead")


# ===================================================================
# Runner
# ===================================================================

if __name__ == "__main__":
    # Section 1
    test_unicode_normalization()
    test_stemmer()
    test_stemmed_tokens()
    test_bigram_matching()
    test_rarity_weighting()
    test_query_intent_classifier()
    test_paper_score_with_stems()
    print("Section 1 (Scoring Engine): 7/7 passed\n")

    # Section 2
    test_profile_registry()
    test_profile_lookup()
    test_query_terms_filtering()
    test_term_match_ratio()
    test_category_match()
    test_field_aware_citations()
    test_venue_prestige()
    test_profile_scoring()
    test_enhanced_paper_score()
    test_ranking_profiles()
    test_agent_skill_backward_compat()
    test_scoring_weights_config()
    print("Section 2 (Profile System): 12/12 passed\n")

    # Section 3
    print("Section 3 (Benchmarks):")
    benchmark()

    print("\n" + "=" * 60)
    print("ALL PAPER ENGINE TESTS PASSED!")
    print("=" * 60)
