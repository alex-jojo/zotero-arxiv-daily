"""Export Zotero interests and today's arXiv candidates for a Codex task."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import feedparser
from pyzotero import zotero


ABSTRACT_PREFIX = re.compile(r"^\s*arXiv:.*?Abstract:\s*", re.DOTALL)


def collection_paths(collections: dict[str, dict], keys: list[str]) -> list[str]:
    def path_for(key: str) -> str:
        data = collections[key]["data"]
        parent = data.get("parentCollection")
        return f"{path_for(parent)}/{data['name']}" if parent else data["name"]

    return [path_for(key) for key in keys if key in collections]


def fetch_zotero_corpus() -> list[dict]:
    client = zotero.Zotero(os.environ["ZOTERO_ID"], "user", os.environ["ZOTERO_KEY"])
    collections = {
        item["key"]: item for item in client.everything(client.collections())
    }
    items = client.everything(
        client.items(itemType="conferencePaper || journalArticle || preprint")
    )
    corpus = []
    for item in items:
        data = item["data"]
        abstract = data.get("abstractNote", "").strip()
        if not abstract:
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
    feed = feedparser.parse(f"https://rss.arxiv.org/atom/{'+'.join(categories)}")
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"Failed to read arXiv RSS: {feed.get('bozo_exception')}")

    candidates = []
    for entry in feed.entries:
        if entry.get("arxiv_announce_type", "new") != "new":
            continue
        paper_id = entry.id.removeprefix("oai:arXiv.org:")
        abstract = ABSTRACT_PREFIX.sub("", entry.get("summary", "")).strip()
        creators = entry.get("author", "")
        authors = [name.strip() for name in creators.split(",") if name.strip()]
        candidates.append(
            {
                "id": paper_id,
                "title": entry.get("title", "").strip(),
                "authors": authors,
                "abstract": abstract,
                "categories": [tag.term for tag in entry.get("tags", [])],
                "url": f"https://arxiv.org/abs/{paper_id}",
                "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
                "published": entry.get("published", ""),
            }
        )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--categories",
        nargs="+",
        default=["cs.AI", "cs.LG", "cs.CL", "cs.CV"],
    )
    args = parser.parse_args()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instructions": {
            "model": "gpt-5.6-sol",
            "paper_count": 5,
            "language": "Chinese",
            "selection_basis": "Match arXiv candidates to the user's Zotero research interests.",
        },
        "zotero_corpus": fetch_zotero_corpus(),
        "arxiv_candidates": fetch_arxiv_candidates(args.categories),
    }
    if len(payload["zotero_corpus"]) == 0:
        raise RuntimeError("No Zotero papers with abstracts were found")
    if len(payload["arxiv_candidates"]) < 5:
        raise RuntimeError("Fewer than five new arXiv candidates were found")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Exported {len(payload['zotero_corpus'])} Zotero papers and "
        f"{len(payload['arxiv_candidates'])} arXiv candidates"
    )


if __name__ == "__main__":
    main()
