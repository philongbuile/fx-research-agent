"""Push every closed-trade lesson to Hindsight right away (bank `fxra`).

The trading code itself learns from the journal (pattern statistics are read on
every bar). This module makes the same lessons available to Claude/Codex and any
later model through Hindsight within one agent tick, instead of at the next
hourly review. Failed deliveries stay pending in the journal and are retried.
"""

import json
import os
import re
from datetime import datetime, timezone

from .simulation_memory import HindsightDocuments
from .trade_journal import Journal


class HindsightLessons(HindsightDocuments):
    """Same loopback/HTTPS-only, no-redirect transport; documents `fxra-lesson-<position>-<lesson id>`."""

    @staticmethod
    def _document(document):
        if not re.fullmatch(r"fxra-lesson-\d+-\d+|fxra-insight-[a-z0-9_-]+", document):
            raise ValueError("lesson or insight document id required")
        return document

    def retain_lesson(self, document: str, content: str, at: str, tags: list[str]):
        self._document(document)
        result = self._request("/memories", {"items": [{"document_id": document, "content": content,
                                                        "timestamp": at, "tags": tags}], "async": True})
        if result.get("success") is not True:
            raise RuntimeError("Hindsight retain not acknowledged")

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("HINDSIGHT_URL", "http://127.0.0.1:8888"), os.environ.get("HINDSIGHT_API_KEY", ""),
                   timeout=10)


def facet_tags(trade: dict, features: dict) -> list[str]:
    """`key:value` tags so recall can filter lessons by outcome, exit, time and market regime."""
    out = []
    if trade.get("net") is not None:
        out.append("outcome:" + ("win" if trade["net"] > 0 else "loss" if trade["net"] < 0 else "flat"))
    if trade.get("exit_reason"):
        out.append(f"exit:{trade['exit_reason']}")
    if trade.get("hour_utc") is not None:
        out.append(f"hour:{int(trade['hour_utc']):02d}")
    dow = features.get("dow") or (datetime.fromisoformat(trade["open_time"]).astimezone(timezone.utc).strftime("%a")
                                  if trade.get("open_time") else None)
    if dow:
        out.append(f"dow:{dow}")
    out += [f"{k}:{features[k]}" for k in ("vol", "h1_trend", "m15_trend") if features.get(k)]
    return out


def pending(journal: Journal, limit: int = 20) -> list[dict]:
    rows = journal._rows("SELECT id, at, position_id, author, text, tags FROM lessons WHERE hindsight_document IS NULL"
                         " ORDER BY id LIMIT ?", limit)
    out = []
    for r in rows:
        trade = journal.trade(r["position_id"]) if r["position_id"] else {}
        tags = ["demo", "lesson", f"author:{r['author']}"] + json.loads(r.pop("tags") or "[]")
        tags += [str(trade[k]) for k in ("strategy", "symbol", "pattern", "session") if trade.get(k)]
        if trade.get("arm"):
            tags.append(f"H-002-{trade['arm']}")
        if trade.get("arm_model"):
            tags.append(f"model:{trade['arm_model']}")
        tags += facet_tags(trade, journal.entry_info(trade["entry_id"])["features"]) if trade else []
        out.append({**r, "document": f"fxra-lesson-{r['position_id'] or 0}-{r['id']}", "tags": tags,
                    "content": r["text"] + (f" Arm model: {trade['arm_model']}." if trade.get("arm_model") else "")})
    return out


def flush(journal: Journal, backend, lock=None) -> dict:
    """Retain pending lessons; network calls run outside `lock`, journal access inside it."""
    import contextlib
    guard = lock or contextlib.nullcontext()
    with guard:
        todo = pending(journal)
    sent, failed = [], 0
    for item in todo:
        try:
            backend.retain_lesson(item["document"], item["content"], item["at"], item["tags"])
        except Exception:
            failed += 1
            break  # Hindsight unreachable: keep the rest pending, retry next tick
        with guard:
            journal.mark_retained(item["id"], item["document"])
        sent.append(item["document"])
    return {"retained": sent, "failed": failed, "pending": len(todo) - len(sent),
            "at": datetime.now(timezone.utc).isoformat()}
