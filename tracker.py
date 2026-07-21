#!/usr/bin/env python3
"""Read CSTNET mail through read-only IMAP and build a privacy-safe Pages site."""

from __future__ import annotations

import argparse
import datetime as dt
import email
import email.header
import email.policy
import hashlib
import html
import imaplib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "private"
DB_PATH = PRIVATE / "tracker.sqlite3"
CONFIG_PATH = ROOT / "config.json"
DOCS = ROOT / "docs"

STATUS_RULES = [
    ("accepted", r"accepted for publication|has been accepted|we are pleased to inform.{0,120}accepted|正式录用|录用通知"),
    ("minor revision", r"minor revision|小修"),
    ("major revision", r"major revision|大修"),
    ("rejected", r"reject(?:ed|ion)|decline(?:d)?|拒稿|未予录用"),
    ("under review", r"under review|with editor|editor assigned|送审|审稿中"),
    ("submitted", r"confirming (?:your )?submission|manuscript received|receipt of manuscript|submission notification|co-authorship|verify your contribution|view your submission|thank you for submitting"),
]

REVIEWER_MAIL = re.compile(
    r"review\s+request|invitation\s+to\s+review|review\s+invitation|review\s+request\s+reminder|"
    r"reviewer notification|independent review report|agreeing to review|"
    r"thank you for reviewing|review forum|\bfor review\b",
    re.I,
)

AUTHOR_MAIL = re.compile(
    r"confirm(?:ing)? (?:your )?(?:co-?authorship|submission)|verify your contribution|"
    r"your submission|manuscript .+ assigned to editor|production has begun on your article|"
    r"article processing charge|listed you as (?:an? )?(?:author|co-?author)|"
    r"thank you for submitting (?:your )?(?:manuscript|article)|"
    r"your pdf has been built|action recommended:\s*view\s+your submission",
    re.I,
)

TITLE_PATTERNS = [
    r"(?:submission |manuscript )?title\s*[:：]\s*[\"“]?(.*?)[\"”]?(?:\r?\n|manuscript id|article type|journal\s*:|$)",
    r"co-author on the manuscript\s*[\"“](.*?)[\"”]",
    r"submission entitled\s*[\"“](.*?)[\"”]",
    r"received your article\s*[\"“](.*?)[\"”]",
    r"manuscript\s*[\"“](.*?)[\"”]\s*\(reference number",
]

ID_PATTERNS = [
    r"(?:manuscript|submission|reference)\s*(?:id|number|no\.?|#)\s*[:：]?\s*([A-Z0-9][A-Z0-9._/-]{4,})",
    r"\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-D-\d{2}-\d+(?:R\d+)?)\b",
]

VENUE_PATTERN = re.compile(r"(?:journal|submitted to|submission to)\s*[:：]?\s*([^\n\r.]{3,100})", re.I)


@dataclass
class ParsedMail:
    uid: int
    message_id: str
    date: str
    subject: str
    sender: str
    title: str
    venue: str
    manuscript_id: str
    base_manuscript_id: str
    status: str
    role: str
    topic: str
    fingerprint: str


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit("Missing config.json; copy config.example.json first.")
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def decode_header(value: str | None) -> str:
    if not value:
        return ""
    chunks = []
    for part, charset in email.header.decode_header(value):
        if isinstance(part, bytes):
            chunks.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            chunks.append(part)
    return "".join(chunks).strip()


def raw_header(msg: Message, name: str) -> str:
    """Return a header without invoking strict structured-header parsing.

    Some real-world mail systems emit malformed Message-ID values that crash
    Python 3.9's header registry. ``raw_items`` preserves those values as text,
    which is sufficient for indexing and display.
    """
    wanted = name.casefold()
    for header_name, value in msg.raw_items():
        if header_name.casefold() == wanted:
            return value.strip()
    return ""


def message_text(msg: Message) -> str:
    chunks: list[str] = []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            text = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/html":
            text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", "\n", text)
            text = html.unescape(text)
        chunks.append(text)
    return re.sub(r"[\t ]+", " ", "\n".join(chunks))


def clean_title(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n\"'“”:-")
    return value[:500]


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def base_id(value: str) -> str:
    return re.sub(r"(?:[._-]?R\d+)$", "", value, flags=re.I).upper()


def infer_topic(title: str) -> str:
    low = title.lower()
    if re.search(r"protein|rna|antibody|spatial|transcriptom|omics|biomedical|bioinform", low):
        return "Bioinformatics"
    if re.search(r"url|phishing|fraud|blockchain|ethereum|vulnerab|smart contract|malicious|security", low):
        return "Security"
    if re.search(r"teaching|education|student", low):
        return "Education AI"
    return "Other"


def infer_status(subject: str, body: str) -> str:
    text = f"{subject}\n{body}"
    if re.search(r"production has begun|article processing charge", subject, re.I):
        return "accepted"
    if re.search(r"confirm(?:ing)? (?:your )?(?:co-?authorship|submission)|verify your contribution", subject, re.I):
        return "submitted"
    for name, pattern in STATUS_RULES:
        if re.search(pattern, text, re.I | re.S):
            return name
    return "submitted"


def clean_venue(venue: str, sender: str, subject: str, manuscript_id: str) -> str:
    production = re.search(r"production has begun.+? in ([^\[\]\r\n]+)$", subject, re.I)
    if production:
        return clean_title(production.group(1))
    if manuscript_id.upper().startswith("T-IFS-"):
        return "IEEE Transactions on Information Forensics and Security"

    display_name = email.utils.parseaddr(sender)[0].strip(' "')
    if re.search(r"editorialmanager\.com|manuscriptcentral\.com|researchexchange\.com", sender, re.I):
        if display_name:
            return display_name.replace("&", "and")

    invalid = re.search(r"@|username=|mailbox|/data|article transfer|peer review service|apc support", venue, re.I)
    if venue and not invalid:
        return venue.replace("&", "and")

    if display_name and not re.search(r"peer review service|editorial office|apc support", display_name, re.I):
        return display_name.replace("&", "and")
    return ""


def parse_message(uid: int, raw: bytes) -> ParsedMail | None:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = decode_header(raw_header(msg, "Subject"))
    sender = decode_header(raw_header(msg, "From"))
    body = message_text(msg)
    combined = f"{subject}\n{body}"
    low = combined.lower()

    if REVIEWER_MAIL.search(subject) or re.search(r"call for papers|submit your manuscript to|special issue invitation", low):
        return None
    explicit_body_author = re.search(r"listed you as (?:an? )?(?:author|co-?author)|you are listed as a co-?author", body, re.I)
    if not AUTHOR_MAIL.search(subject) and not explicit_body_author:
        return None

    title = ""
    for pattern in TITLE_PATTERNS:
        match = re.search(pattern, combined, re.I | re.S)
        if match:
            title = clean_title(match.group(1))
            if 8 <= len(title) <= 500:
                break
            title = ""
    if not title:
        return None

    manuscript_id = ""
    for pattern in ID_PATTERNS:
        match = re.search(pattern, combined, re.I)
        if match:
            manuscript_id = match.group(1).strip(".,;:()[] ")
            break

    venue = ""
    match = VENUE_PATTERN.search(combined)
    if match:
        venue = clean_title(match.group(1))
    if not venue:
        venue = re.sub(r"[\"<>].*", "", sender).strip()[:150]
    venue = clean_venue(venue, sender, subject, manuscript_id)

    status = infer_status(subject, body)
    role = "co-author" if re.search(r"co-author|coauthorship|co-authorship|verify your contribution|listed you as", low) else "submitting author"

    raw_date = raw_header(msg, "Date")
    try:
        parsed_date = email.utils.parsedate_to_datetime(raw_date) if raw_date else None
    except (TypeError, ValueError, OverflowError):
        parsed_date = None
    if parsed_date:
        parsed_date = parsed_date.astimezone(dt.timezone.utc)
        date = parsed_date.date().isoformat()
    else:
        date = dt.date.today().isoformat()

    norm = normalize_title(title)
    fingerprint = hashlib.sha256(norm.encode()).hexdigest()[:24]
    return ParsedMail(
        uid=uid,
        message_id=raw_header(msg, "Message-ID"),
        date=date,
        subject=subject,
        sender=sender,
        title=title,
        venue=venue,
        manuscript_id=manuscript_id,
        base_manuscript_id=base_id(manuscript_id),
        status=status,
        role=role,
        topic=infer_topic(title),
        fingerprint=fingerprint,
    )


def connect(cfg: dict) -> imaplib.IMAP4_SSL:
    password = os.environ.get("CSTNET_IMAP_PASSWORD", "")
    if not password and sys.platform == "darwin":
        service = cfg["mail"].get("keychain_service", "cstnet-paper-tracker")
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            check=False,
            capture_output=True,
            text=True,
        )
        password = result.stdout.strip()
    if not password:
        raise SystemExit("No IMAP password found. Store a CSTNET client-specific password in Keychain first.")
    client = imaplib.IMAP4_SSL(cfg["mail"]["host"], int(cfg["mail"]["port"]))
    client.login(cfg["mail"]["username"], password)
    return client


def open_db() -> sqlite3.Connection:
    PRIVATE.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
          uid INTEGER PRIMARY KEY, message_id TEXT, received_date TEXT,
          subject TEXT, sender TEXT, parsed_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS submissions (
          fingerprint TEXT PRIMARY KEY, title TEXT NOT NULL, venue TEXT,
          manuscript_id TEXT, base_manuscript_id TEXT, status TEXT,
          role TEXT, topic TEXT, submitted_date TEXT, updated_date TEXT
        );
        """
    )
    return db


def scan(cfg: dict) -> int:
    db = open_db()
    last_uid = db.execute("SELECT COALESCE(MAX(uid), 0) FROM messages").fetchone()[0]
    client = connect(cfg)
    try:
        status, _ = client.select(cfg["mail"].get("mailbox", "INBOX"), readonly=True)
        if status != "OK":
            raise RuntimeError("Could not open mailbox in read-only mode")
        if last_uid:
            criterion = f"UID {last_uid + 1}:*"
        else:
            lookback_days = int(cfg["mail"].get("lookback_days", 30))
            since = dt.date.today() - dt.timedelta(days=lookback_days)
            criterion = f'SINCE "{since.strftime("%d-%b-%Y")}"'
            print(f"First scan: checking INBOX messages from {since} onward ({lookback_days} days).", flush=True)
        status, data = client.uid("search", None, criterion)
        if status != "OK":
            raise RuntimeError("IMAP search failed")
        uids = data[0].split()
        total = len(uids)
        print(f"Found {total} messages to inspect.", flush=True)
        if not total:
            return 0
        started = time.monotonic()
        count = 0
        for index, uid_raw in enumerate(uids, start=1):
            uid = int(uid_raw)
            status, fetched = client.uid("fetch", str(uid), "(RFC822)")
            if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            parsed = parse_message(uid, fetched[0][1])
            if not parsed:
                db.execute(
                    "INSERT OR IGNORE INTO messages VALUES (?, '', '', '', '', ?)",
                    (uid, json.dumps({"ignored": True})),
                )
            else:
                payload = json.dumps(asdict(parsed), ensure_ascii=False)
                db.execute(
                    "INSERT OR REPLACE INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, parsed.message_id, parsed.date, parsed.subject, parsed.sender, payload),
                )
                existing = db.execute("SELECT submitted_date FROM submissions WHERE fingerprint=?", (parsed.fingerprint,)).fetchone()
                submitted = min(existing[0], parsed.date) if existing else parsed.date
                db.execute(
                    """INSERT OR REPLACE INTO submissions
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (parsed.fingerprint, parsed.title, parsed.venue, parsed.manuscript_id,
                     parsed.base_manuscript_id, parsed.status, parsed.role, parsed.topic,
                     submitted, parsed.date),
                )
                count += 1

            if index % 25 == 0 or index == total:
                db.commit()
                elapsed = time.monotonic() - started
                rate = index / elapsed if elapsed else 0
                remaining = (total - index) / rate if rate else 0
                print(
                    f"Progress {index}/{total} ({index / total:.0%}) | "
                    f"matched {count} | elapsed {elapsed / 60:.1f} min | "
                    f"ETA {remaining / 60:.1f} min",
                    flush=True,
                )
        db.commit()
        return count
    finally:
        try:
            client.logout()
        except Exception:
            pass


def export_public(cfg: dict) -> int:
    db = open_db()
    rows = db.execute(
        """SELECT title, venue, status, role, topic, submitted_date, updated_date
        FROM submissions ORDER BY updated_date DESC, title"""
    ).fetchall()
    show_rejected = cfg.get("public", {}).get("show_rejected", False)
    items = []
    for title, venue, status, role, topic, submitted, updated in rows:
        if status == "rejected" and not show_rejected:
            continue
        items.append({
            "title": title,
            "venue": venue,
            "status": status,
            "role": role,
            "topic": topic,
            "submitted_month": submitted[:7],
            "updated_date": updated,
        })
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.json").write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan", "export", "run"])
    args = parser.parse_args()
    cfg = load_config()
    if args.command in {"scan", "run"}:
        print(f"Parsed {scan(cfg)} new submission-related messages")
    if args.command in {"export", "run"}:
        print(f"Exported {export_public(cfg)} public submissions")


if __name__ == "__main__":
    main()
