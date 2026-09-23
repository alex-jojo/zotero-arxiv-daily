"""Tests for ArxivRetriever."""

import time
from types import SimpleNamespace

import feedparser

from zotero_arxiv_daily.retriever.arxiv_retriever import (
    ArxivRetriever,
    _rss_entry_to_result,
    _run_with_hard_timeout,
)
import zotero_arxiv_daily.retriever.arxiv_retriever as arxiv_retriever


def _sleep_and_return(value: str, delay_seconds: float) -> str:
    time.sleep(delay_seconds)
    return value


def _raise_runtime_error() -> None:
    raise RuntimeError("boom")


def test_arxiv_retriever(config, mock_feedparser, monkeypatch):
    monkeypatch.setattr("zotero_arxiv_daily.retriever.base.sleep", lambda _: None)

    new_entries = [
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") == "new"
    ]

    # Skip file downloads in convert_to_paper
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()

    assert len(papers) == len(new_entries)
    assert set(p.title for p in papers) == set(e.title for e in new_entries)


def test_rss_entry_to_result_uses_rss_metadata(mock_feedparser):
    entry = mock_feedparser.entries[-1]

    result = _rss_entry_to_result(entry)

    assert result.entry_id == "https://arxiv.org/abs/2508.14002v1"
    assert result.pdf_url == "https://arxiv.org/pdf/2508.14002v1"
    assert result.summary == (
        "We study reward shaping techniques in cooperative multi-agent settings."
    )
    assert [author.name for author in result.authors] == ["Carol White", "Dave Brown"]
    assert result.categories == ["cs.AI", "cs.MA"]


def test_convert_to_paper_skips_full_text_when_llm_is_disabled(config, monkeypatch):
    from omegaconf import open_dict

    with open_dict(config):
        config.llm.enabled = False

    def fail_if_called(paper):
        raise AssertionError("Full text extraction should be skipped when LLM is disabled")

    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", fail_if_called)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", fail_if_called)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", fail_if_called)

    raw_paper = SimpleNamespace(
        title="No LLM Paper",
        authors=[SimpleNamespace(name="Test Author")],
        summary="Original abstract.",
        pdf_url="https://arxiv.org/pdf/2601.00001",
        entry_id="https://arxiv.org/abs/2601.00001",
    )

    paper = ArxivRetriever(config).convert_to_paper(raw_paper)

    assert paper.full_text is None
    assert paper.abstract == "Original abstract."


def test_run_with_hard_timeout_returns_value():
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 0.01), timeout=1, operation="test op", paper_title="paper"
    )
    assert result == "done"


def test_run_with_hard_timeout_returns_none_on_timeout(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 1.0), timeout=0.01, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "timed out" in warnings[0]


def test_run_with_hard_timeout_returns_none_on_failure(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _raise_runtime_error, (), timeout=1, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "boom" in warnings[0]
