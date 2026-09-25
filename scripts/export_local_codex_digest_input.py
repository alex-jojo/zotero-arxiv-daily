"""Export local Zotero interests and today's arXiv candidates for Codex."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import feedparser


ZOTERO_EXE = Path(r"C:\Program Files\Zotero\zotero.exe")
ZOTERO_API = "http://127.0.0.1:23119/api"
USER_ID = "21011592"
ABSTRACT_PREFIX = re.compile(r"^\s*arXiv:.*?Abstract:\s*", re.DOTALL)
WORD_PATTERN = re.compile(r"[a-z][a-z0-9-]{2,}")
STOP_WORDS = {
    "the", "and", "for", "that", "with", "this", "from", "are", "was",
    "were", "have", "has", "using", "use", "our", "their", "these", "its",
    "into", "which", "can", "paper", "method", "results", "show", "based",
}


def urlopen_no_proxy(url: str, timeout: int = 30):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "zotero-arxiv-daily-codex/1.0"},
    )
    return opener.open(request, timeout=timeout)


def urlopen_with_proxy_fallback(url: str, timeout: int = 30):
    """Use the user's configured proxy first, then try a direct connection."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "zotero-arxiv-daily-codex/1.0"},
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except Exception as proxy_error:
        try:
            return urlopen_no_proxy(url, timeout=timeout)
        except Exception as direct_error:
            raise RuntimeError(
                f"Could not fetch {url} through the configured proxy or directly: "
                f"proxy={proxy_error!r}; direct={direct_error!r}"
            ) from direct_error


def fetch_json(url: str) -> list[dict]:
    with urlopen_no_proxy(url) as response:
        return json.loads(response.read().decode("utf-8"))


def ensure_zotero_running() -> None:
    probe = f"{ZOTERO_API}/users/{USER_ID}/items?limit=1&format=json"
    try:
        fetch_json(probe)
        return
    except Exception:
        if not ZOTERO_EXE.exists():
            raise RuntimeError(f"Zotero executable not found at {ZOTERO_EXE}")
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([str(ZOTERO_EXE)], creationflags=creation_flags)

    for _ in range(30):
        time.sleep(1)
        try:
            fetch_json(probe)
            return
        except Exception:
            continue
    raise RuntimeError("Zotero local API did not become available within 30 seconds")


def fetch_all(endpoint: str, **parameters: str) -> list[dict]:
    results: list[dict] = []
    start = 0
    while True:
        query = urllib.parse.urlencode({**parameters, "start": start, "limit": 100})
        page = fetch_json(f"{ZOTERO_API}/users/{USER_ID}/{endpoint}?{query}")
        results.extend(page)
        if len(page) < 100:
            return results
        start += len(page)


def collection_paths(collections: dict[str, dict], keys: list[str]) -> list[str]:
    def path_for(key: str) -> str:
        data = collections[key]["data"]
        parent = data.get("parentCollection")
        return f"{path_for(parent)}/{data['name']}" if parent else data["name"]

    return [path_for(key) for key in keys if key in collections]


def fetch_zotero_corpus() -> list[dict]:
    ensure_zotero_running()
    collections = {item["key"]: item for item in fetch_all("collections")}
    allowed_types = {"conferencePaper", "journalArticle", "preprint"}
    corpus = []
    for item in fetch_all("items", format="json"):
        data = item["data"]
        abstract = data.get("abstractNote", "").strip()
        if data.get("itemType") not in allowed_types or not abstract:
            continue
        corpus.append(
            {
                "title": data.get("title", "").strip(),
                "abstract": abstract,
                "date_added": data.get("dateAdded", ""),
                "collections": collection_paths(collections, data.get("collections", [])),
            }
        )
    corpus.sort(key=lambda paper: paper["date_added"], reverse=True)
    return corpus


def fetch_arxiv_candidates(categories: list[str]) -> list[dict]:
    url = f"https://rss.arxiv.org/atom/{'+'.join(categories)}"
    with urlopen_with_proxy_fallback(url, timeout=60) as response:
        feed = feedparser.parse(response.read())
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"Failed to read arXiv RSS: {feed.get('bozo_exception')}")

    candidates = []
    for entry in feed.entries:
        if entry.get("arxiv_announce_type", "new") != "new":
            continue
        paper_id = entry.id.removeprefix("oai:arXiv.org:")
        candidates.append(
            {
                "id": paper_id,
                "title": entry.get("title", "").strip(),
                "authors": [
                    name.strip()
                    for name in entry.get("author", "").split(",")
                    if name.strip()
                ],
                "abstract": ABSTRACT_PREFIX.sub("", entry.get("summary", "")).strip(),
                "categories": [tag.term for tag in entry.get("tags", [])],
                "url": f"https://arxiv.org/abs/{paper_id}",
                "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
                "published": entry.get("published", ""),
            }
        )
    return candidates


def prefilter_candidates(
    corpus: list[dict], candidates: list[dict], limit: int
) -> list[dict]:
    """Cheaply reduce the GPT input; GPT still makes the final selection."""
    def tokenize(text: str) -> list[str]:
        return [word for word in WORD_PATTERN.findall(text.lower()) if word not in STOP_WORDS]

    corpus_tokens = [
        tokenize(f"{paper['title']} {paper['abstract']}") for paper in corpus
    ]
    document_frequency: Counter[str] = Counter()
    for tokens in corpus_tokens:
        document_frequency.update(set(tokens))
    corpus_size = len(corpus_tokens)
    idf = {
        term: math.log((corpus_size + 1) / (frequency + 1)) + 1
        for term, frequency in document_frequency.items()
    }

    profile: Counter[str] = Counter()
    for index, tokens in enumerate(corpus_tokens):
        counts = Counter(tokens)
        recency_weight = 1 / (1 + math.log10(index + 1))
        length = max(len(tokens), 1)
        for term, count in counts.items():
            profile[term] += recency_weight * (count / length) * idf[term]
    profile_norm = math.sqrt(sum(weight * weight for weight in profile.values())) or 1

    scores = []
    for paper in candidates:
        tokens = tokenize(f"{paper['title']} {paper['abstract']}")
        counts = Counter(tokens)
        length = max(len(tokens), 1)
        weighted = {
            term: (count / length) * idf.get(term, 0)
            for term, count in counts.items()
            if term in profile
        }
        candidate_norm = math.sqrt(sum(value * value for value in weighted.values())) or 1
        dot_product = sum(value * profile[term] for term, value in weighted.items())
        scores.append(dot_product / (candidate_norm * profile_norm))

    ranked_indices = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)[:limit]

    selected = []
    for index in ranked_indices:
        paper = candidates[index]
        paper["prefilter_score"] = round(float(scores[index]), 6)
        selected.append(paper)
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--categories",
        nargs="+",
        default=["cs.AI", "cs.LG", "cs.CL", "cs.CV"],
    )
    parser.add_argument("--candidate-limit", type=int, default=30)
    args = parser.parse_args()
    corpus = fetch_zotero_corpus()
    all_candidates = fetch_arxiv_candidates(args.categories)
    candidates = prefilter_candidates(corpus, all_candidates, args.candidate_limit)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instructions": {
            "model": "gpt-5.6-sol",
            "paper_count": 3,
            "language": "Chinese",
        },
        "zotero_corpus": corpus,
        "arxiv_candidates": candidates,
        "candidate_counts": {
            "before_prefilter": len(all_candidates),
            "after_prefilter": len(candidates),
        },
    }
    if not payload["zotero_corpus"]:
        raise RuntimeError("No Zotero papers with abstracts were found")
    if len(payload["arxiv_candidates"]) < 3:
        raise RuntimeError("Fewer than three new arXiv candidates were found")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Exported {len(payload['zotero_corpus'])} local Zotero papers and "
        f"{len(all_candidates)} arXiv candidates; kept {len(candidates)} for GPT"
    )


if __name__ == "__main__":
    main()
