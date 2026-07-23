from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

RULEBOOK_PATH = Path(__file__).resolve().parents[1] / "data" / "moderation_rules.json"


@dataclass(frozen=True, slots=True)
class ModerationRule:
    rule_id: str
    category: str
    severity: str
    reason: str
    scopes: frozenset[str]
    patterns: tuple[re.Pattern[str], ...]
    exclude_patterns: tuple[re.Pattern[str], ...]
    confidence: str = "high"
    max_matches: int = 20


@dataclass(frozen=True, slots=True)
class RuleMatch:
    rule_id: str
    category: str
    severity: str
    reason: str
    confidence: str
    start: int
    end: int
    matched_text: str


def _compile_many(values: Iterable[object]) -> tuple[re.Pattern[str], ...]:
    result: list[re.Pattern[str]] = []
    for value in values:
        pattern = str(value or "").strip()
        if not pattern:
            continue
        result.append(re.compile(pattern, re.IGNORECASE | re.UNICODE | re.DOTALL))
    return tuple(result)


@lru_cache(maxsize=1)
def load_moderation_rulebook() -> tuple[ModerationRule, ...]:
    """Load the deterministic moderation knowledge base bundled with the app.

    The rulebook intentionally contains only high-confidence, context-aware
    patterns. Ambiguous words such as «ставки», «казино» or «реклама» are not
    violations by themselves and therefore never appear as standalone rules.
    """
    try:
        payload = json.loads(RULEBOOK_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Could not load moderation rulebook: %s", RULEBOOK_PATH)
        return ()

    rules: list[ModerationRule] = []
    for item in payload.get("rules", []):
        try:
            rule_id = str(item["id"]).strip()
            patterns = _compile_many(item.get("patterns", []))
            if not rule_id or not patterns:
                continue
            scopes = frozenset(str(scope).strip().lower() for scope in item.get("scopes", ["text", "metadata"]) if str(scope).strip())
            rules.append(
                ModerationRule(
                    rule_id=rule_id,
                    category=str(item.get("category") or "promotion").strip(),
                    severity=str(item.get("severity") or "block").strip(),
                    reason=str(item.get("reason") or "Требуется проверка").strip(),
                    scopes=scopes,
                    patterns=patterns,
                    exclude_patterns=_compile_many(item.get("exclude_patterns", [])),
                    confidence=str(item.get("confidence") or "high").strip(),
                    max_matches=max(1, min(100, int(item.get("max_matches", 20) or 20))),
                )
            )
        except Exception:
            logger.exception("Invalid moderation rule: %r", item)
    return tuple(rules)


def rulebook_version() -> str:
    try:
        payload = json.loads(RULEBOOK_PATH.read_text(encoding="utf-8"))
        return str(payload.get("version") or "unknown")
    except Exception:
        return "unknown"


def scan_rulebook(text: str, *, scope: str) -> list[RuleMatch]:
    """Return only context-supported violations from the rule knowledge base."""
    source = str(text or "")
    normalized_scope = str(scope or "text").strip().lower()
    if not source:
        return []

    found: list[RuleMatch] = []
    seen: set[tuple[str, int, int]] = set()
    for rule in load_moderation_rulebook():
        if normalized_scope not in rule.scopes:
            continue
        emitted = 0
        for pattern in rule.patterns:
            for match in pattern.finditer(source):
                if emitted >= rule.max_matches:
                    break
                left = max(0, match.start() - 120)
                right = min(len(source), match.end() + 120)
                context = source[left:right]
                if any(exclusion.search(context) for exclusion in rule.exclude_patterns):
                    continue
                key = (rule.rule_id, match.start(), match.end())
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    RuleMatch(
                        rule_id=rule.rule_id,
                        category=rule.category,
                        severity=rule.severity,
                        reason=rule.reason,
                        confidence=rule.confidence,
                        start=match.start(),
                        end=match.end(),
                        matched_text=match.group(0),
                    )
                )
                emitted += 1
    found.sort(key=lambda item: (item.rule_id, item.start, -(item.end - item.start)))
    compacted: list[RuleMatch] = []
    for item in found:
        overlapping_index = next((
            index for index, previous in enumerate(compacted)
            if previous.rule_id == item.rule_id
            and item.start < previous.end
            and previous.start < item.end
        ), None)
        if overlapping_index is None:
            compacted.append(item)
            continue
        previous = compacted[overlapping_index]
        if (item.end - item.start) > (previous.end - previous.start):
            compacted[overlapping_index] = item
    compacted.sort(key=lambda item: (item.start, item.end, item.rule_id))
    return compacted
