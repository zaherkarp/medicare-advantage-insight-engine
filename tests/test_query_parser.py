"""Tests for the rule-based natural-language archive query translator."""

from datetime import datetime

from ma_signal_monitor.query_parser import parse_query

_NOW = datetime(2026, 8, 3, 12, 0)


class TestDateExtraction:
    def test_since_month_this_year(self, sample_config):
        parsed = parse_query("signals since March", sample_config, now=_NOW)
        assert parsed.since == "2026-03-01"

    def test_since_month_rolls_back_a_year_if_future(self, sample_config):
        # "since December" asked in August means last December, not next.
        parsed = parse_query("signals since December", sample_config, now=_NOW)
        assert parsed.since == "2025-12-01"

    def test_since_month_with_explicit_year(self, sample_config):
        parsed = parse_query("signals since March 2024", sample_config, now=_NOW)
        assert parsed.since == "2024-03-01"

    def test_last_n_days(self, sample_config):
        parsed = parse_query("signals from the last 30 days", sample_config, now=_NOW)
        assert parsed.since == "2026-07-04"

    def test_this_week(self, sample_config):
        parsed = parse_query("signals this week", sample_config, now=_NOW)
        # _NOW is a Monday (2026-08-03); week start is the same day.
        assert parsed.since == "2026-08-03"

    def test_this_month(self, sample_config):
        parsed = parse_query("signals this month", sample_config, now=_NOW)
        assert parsed.since == "2026-08-01"

    def test_in_year(self, sample_config):
        parsed = parse_query("signals in 2024", sample_config, now=_NOW)
        assert parsed.since == "2024-01-01"

    def test_no_date_phrase(self, sample_config):
        parsed = parse_query("Humana enrollment", sample_config, now=_NOW)
        assert parsed.since is None


class TestScoreTier:
    def test_alert_grade(self, sample_config):
        parsed = parse_query("everything above alert grade", sample_config, now=_NOW)
        assert parsed.min_score == sample_config.min_relevance_score
        assert "alert grade" in parsed.min_score_label

    def test_archive_floor(self, sample_config):
        parsed = parse_query("everything above archive floor", sample_config, now=_NOW)
        assert parsed.min_score == sample_config.archive_min_score

    def test_explicit_score(self, sample_config):
        parsed = parse_query("stories with score above 0.5", sample_config, now=_NOW)
        assert parsed.min_score == 0.5

    def test_default_is_archive_floor(self, sample_config):
        parsed = parse_query("Humana enrollment", sample_config, now=_NOW)
        assert parsed.min_score == sample_config.archive_min_score


class TestStateAndEntity:
    def test_state_name_detected(self, sample_config):
        parsed = parse_query("stories in Texas", sample_config, now=_NOW)
        assert parsed.state == "TX"

    def test_entity_detected(self, sample_config):
        parsed = parse_query("stories about Humana", sample_config, now=_NOW)
        assert parsed.entity_aliases == ["Humana"]

    def test_multiple_entities_detected(self, sample_config):
        parsed = parse_query("stories about Humana and Aetna", sample_config, now=_NOW)
        assert set(parsed.entity_aliases) == {"Humana", "Aetna"}

    def test_no_entity_no_state(self, sample_config):
        parsed = parse_query("stories about margins", sample_config, now=_NOW)
        assert parsed.entity_aliases == []
        assert parsed.state is None


class TestCategory:
    def test_category_matched_via_keyword(self, sample_config):
        parsed = parse_query("stories about star ratings", sample_config, now=_NOW)
        assert parsed.category == "policy_regulatory"

    def test_category_keyword_plural_tolerant(self, sample_config):
        # "star rating" is the taxonomy keyword; the question says "ratings".
        parsed = parse_query("star ratings changes", sample_config, now=_NOW)
        assert parsed.category == "policy_regulatory"

    def test_no_category_match_leaves_it_none(self, sample_config):
        parsed = parse_query("measure set changes", sample_config, now=_NOW)
        assert parsed.category is None


class TestKeywordFallback:
    def test_unmatched_text_becomes_keywords(self, sample_config):
        parsed = parse_query("measure set changes", sample_config, now=_NOW)
        assert parsed.keywords == "measure set changes"

    def test_stopwords_and_matched_clauses_stripped(self, sample_config):
        parsed = parse_query(
            "show me everything above alert grade related to measure set "
            "changes since March",
            sample_config,
            now=_NOW,
        )
        assert parsed.min_score == sample_config.min_relevance_score
        assert parsed.since == "2026-03-01"
        assert parsed.keywords == "measure set changes"

    def test_pure_structured_query_has_no_keywords(self, sample_config):
        parsed = parse_query(
            "Humana stories in Texas above alert grade", sample_config, now=_NOW
        )
        assert parsed.keywords == ""
        assert parsed.entity_aliases == ["Humana"]
        assert parsed.state == "TX"


class TestNotes:
    def test_notes_trace_every_extracted_filter(self, sample_config):
        parsed = parse_query(
            "Humana stories in Texas above alert grade since March",
            sample_config,
            now=_NOW,
        )
        joined = "; ".join(parsed.notes)
        assert "since=2026-03-01" in joined
        assert "alert grade" in joined
        assert "TX" in joined
        assert "Humana" in joined
