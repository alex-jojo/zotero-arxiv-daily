"""Send a GPT-selected digest using SMTP credentials from GitHub Secrets."""

from __future__ import annotations

import base64
import html
import json
import os
import smtplib
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr


def paper_html(paper: dict, index: int) -> str:
    authors = ", ".join(paper.get("authors", [])[:8]) or "Unknown authors"
    score = float(paper["relevance_score"])
    return f"""
    <section style="border:1px solid #ddd;border-radius:8px;padding:16px;margin:0 0 16px;font-family:Arial,sans-serif">
      <h2 style="margin:0 0 8px;font-size:19px">{index}. {html.escape(paper['title'])}</h2>
      <div style="color:#666;margin-bottom:8px">{html.escape(authors)}</div>
      <div><strong>GPT‑5.6 Sol 相关度：</strong>{score:.1f}/10</div>
      <p><strong>推荐理由：</strong>{html.escape(paper['reason_zh'])}</p>
      <p><strong>中文摘要：</strong>{html.escape(paper['summary_zh'])}</p>
      <a href="{html.escape(paper['url'])}">arXiv</a>
      &nbsp;·&nbsp;
      <a href="{html.escape(paper['pdf_url'])}">PDF</a>
    </section>
    """


def validate(payload: dict) -> None:
    if payload.get("model") != "gpt-5.6-sol":
        raise ValueError("Digest must declare model=gpt-5.6-sol")
    papers = payload.get("papers")
    if not isinstance(papers, list) or len(papers) != 3:
        raise ValueError("Digest must contain exactly three papers")
    required = {
        "title", "authors", "url", "pdf_url", "summary_zh", "reason_zh", "relevance_score"
    }
    for paper in papers:
        missing = required.difference(paper)
        if missing:
            raise ValueError(f"Paper is missing fields: {sorted(missing)}")


def main() -> None:
    encoded = os.environ["DIGEST_B64"]
    payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
    validate(payload)

    body = "".join(paper_html(paper, index) for index, paper in enumerate(payload["papers"], 1))
    body = f"""
    <html><body>
      <h1 style="font-family:Arial,sans-serif">今日 Zotero 个性化论文推荐</h1>
      <p style="font-family:Arial,sans-serif;color:#555">由 GPT‑5.6 Sol 根据你的 Zotero 文献筛选。</p>
      {body}
    </body></html>
    """

    sender = os.environ["SENDER"]
    receiver = os.environ["RECEIVER"]
    message = MIMEText(body, "html", "utf-8")
    message["From"] = formataddr((str(Header("Zotero GPT 推荐", "utf-8")), sender))
    message["To"] = receiver
    today = datetime.now().strftime("%Y/%m/%d")
    message["Subject"] = Header(f"GPT‑5.6 Sol 每日论文推荐 {today}", "utf-8").encode()

    with smtplib.SMTP(os.environ.get("SMTP_SERVER", "smtp.gmail.com"), 587) as server:
        server.starttls()
        server.login(sender, os.environ["SENDER_PASSWORD"])
        server.sendmail(sender, [receiver], message.as_string())
    print("GPT-selected digest email sent successfully")


if __name__ == "__main__":
    main()
