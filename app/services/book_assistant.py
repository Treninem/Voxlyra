from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Iterable

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9-]{1,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_CAPITALIZED_RE = re.compile(r"\b[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{2,}){0,2}\b")
_QUOTED_RE = re.compile(r"[«\"]([^»\"]{2,60})[»\"]")
_UPPER_RE = re.compile(r"\b[А-ЯЁA-Z][А-ЯЁA-Z0-9-]{2,}\b")

_STOPWORDS = {
    "это", "как", "что", "кто", "где", "когда", "почему", "зачем", "про", "для", "или", "его", "ее", "её",
    "они", "она", "оно", "был", "была", "были", "будет", "есть", "нет", "уже", "ещё", "еще", "тоже", "только",
    "вот", "там", "тут", "так", "очень", "после", "перед", "через", "между", "над", "под", "при", "без", "из",
    "на", "по", "до", "от", "за", "к", "ко", "во", "в", "с", "со", "и", "а", "но", "да", "не", "ни",
    "мы", "вы", "ты", "я", "он", "их", "нас", "вас", "мне", "ему", "ей", "себя", "свой", "свои", "этот",
    "эта", "эти", "того", "тому", "чтобы", "если", "ли", "же", "бы", "можно", "нужно", "стал", "стала", "сказал",
    "сказала", "глава", "книга", "расскажи", "напомни", "объясни", "такой", "такая", "такое", "сейчас", "раньше",
}

_ENTITY_STOPWORDS = {
    "Глава", "Часть", "Книга", "Утро", "Вечер", "Ночь", "День", "Однако", "Поэтому", "Потом", "Тогда", "Теперь",
    "Когда", "Если", "Пока", "Вдруг", "Снова", "Вскоре", "Затем", "Наконец", "Наверное", "Конечно", "Просто",
    "Впрочем", "Например", "Несмотря", "После", "Перед", "Через", "Вместо", "Рядом", "Здесь", "Там", "Тут",
    "Первый", "Второй", "Третий", "Последний", "Главный", "Новый", "Старый", "Человек", "Мужчина", "Женщина",
}

_TERM_STOPWORDS = _ENTITY_STOPWORDS | {
    "Да", "Нет", "Хорошо", "Спасибо", "Почему", "Куда", "Откуда", "Сколько", "Никто", "Кто-то", "Что-то",
}


def text_digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def clean_text(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def split_sentences(text: str) -> list[str]:
    cleaned = clean_text(text)
    if not cleaned:
        return []
    result: list[str] = []
    for chunk in _SENTENCE_SPLIT_RE.split(cleaned):
        sentence = re.sub(r"\s+", " ", chunk).strip(" \t\n")
        if len(sentence) < 12:
            continue
        if len(sentence) > 520:
            pieces = re.split(r"(?<=[,;:])\s+", sentence)
            buffer = ""
            for piece in pieces:
                candidate = f"{buffer} {piece}".strip()
                if buffer and len(candidate) > 360:
                    result.append(buffer)
                    buffer = piece
                else:
                    buffer = candidate
            if buffer:
                result.append(buffer)
        else:
            result.append(sentence)
    return result


def _tokens(text: str) -> list[str]:
    return [token.lower().replace("ё", "е") for token in _WORD_RE.findall(text or "")]


def question_keywords(question: str, *, limit: int = 8) -> list[str]:
    values: list[str] = []
    for token in _tokens(question):
        if len(token) < 3 or token in _STOPWORDS or token.isdigit():
            continue
        if token not in values:
            values.append(token)
        if len(values) >= limit:
            break
    return values


def summarize_text(text: str, *, max_sentences: int = 3, max_chars: int = 760) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return ""
    if len(sentences) <= max_sentences:
        return " ".join(sentences)[:max_chars].strip()

    frequencies = Counter(
        token
        for sentence in sentences
        for token in _tokens(sentence)
        if len(token) >= 4 and token not in _STOPWORDS and not token.isdigit()
    )
    highest = max(frequencies.values(), default=1)
    scored: list[tuple[float, int, str]] = []
    total = max(1, len(sentences) - 1)
    for index, sentence in enumerate(sentences):
        tokens = [token for token in _tokens(sentence) if len(token) >= 4 and token not in _STOPWORDS]
        lexical = sum(frequencies.get(token, 0) / highest for token in set(tokens)) / max(1, len(set(tokens)))
        position = 0.24 if index == 0 else 0.12 * (1 - index / total)
        length_bonus = 0.12 if 55 <= len(sentence) <= 260 else 0.02
        event_bonus = 0.08 if re.search(r"\b(решил|узнал|наш[её]л|встретил|появил|погиб|исчез|напал|открыл|раскрыл|согласил|отказал|победил|проиграл)\w*\b", sentence.lower()) else 0
        scored.append((lexical + position + length_bonus + event_bonus, index, sentence))

    selected = sorted(sorted(scored, reverse=True)[: max_sentences * 2], key=lambda item: item[1])
    result: list[str] = []
    length = 0
    for _, _, sentence in selected:
        if sentence in result:
            continue
        addition = len(sentence) + (1 if result else 0)
        if result and length + addition > max_chars:
            continue
        result.append(sentence)
        length += addition
        if len(result) >= max_sentences:
            break
    if not result:
        result = sentences[:max_sentences]
    return " ".join(result)[:max_chars].strip()


def extract_characters(text: str, *, limit: int = 12) -> list[dict[str, Any]]:
    sentences = split_sentences(text)
    counts: Counter[str] = Counter()
    first_excerpt: dict[str, str] = {}
    for sentence in sentences:
        for match in _CAPITALIZED_RE.finditer(sentence):
            name = re.sub(r"\s+", " ", match.group(0)).strip()
            parts = name.split()
            if not parts or any(part in _ENTITY_STOPWORDS for part in parts):
                continue
            if len(parts) == 1 and match.start() == 0 and counts[name] == 0:
                # Одно обычное слово в начале предложения не считаем именем без повторения.
                counts[name] += 0
                first_excerpt.setdefault(name, sentence[:240])
                continue
            if len(name) > 48:
                continue
            counts[name] += 1
            first_excerpt.setdefault(name, sentence[:240])
    # Повторно учитываем одиночные имена, которые встречаются хотя бы в двух предложениях.
    raw_text = clean_text(text)
    for candidate in list(first_excerpt):
        if counts[candidate] == 0:
            occurrences = len(re.findall(rf"\b{re.escape(candidate)}\b", raw_text))
            if occurrences >= 2:
                counts[candidate] = occurrences
    items = [
        {"name": name, "count": int(count), "excerpt": first_excerpt.get(name, "")}
        for name, count in counts.most_common()
        if count >= 1
    ]
    items.sort(key=lambda item: (-int(item["count"]), -len(str(item["name"])), str(item["name"])))
    return items[:limit]


def extract_terms(text: str, *, limit: int = 12) -> list[dict[str, Any]]:
    cleaned = clean_text(text)
    counts: Counter[str] = Counter()
    excerpts: dict[str, str] = {}
    sentences = split_sentences(cleaned)

    for sentence in sentences:
        candidates: list[str] = []
        candidates.extend(match.group(1).strip() for match in _QUOTED_RE.finditer(sentence))
        candidates.extend(match.group(0).strip() for match in _UPPER_RE.finditer(sentence))
        candidates.extend(
            token for token in _WORD_RE.findall(sentence)
            if "-" in token and len(token) >= 5 and not token.startswith("-") and not token.endswith("-")
        )
        for term in candidates:
            term = re.sub(r"\s+", " ", term).strip(" .,;:!?—–-\"'«»")
            if len(term) < 3 or len(term) > 60 or term in _TERM_STOPWORDS:
                continue
            lowered = term.lower().replace("ё", "е")
            if lowered in _STOPWORDS:
                continue
            counts[term] += 1
            excerpts.setdefault(term, sentence[:240])

    items = [
        {"term": term, "count": int(count), "excerpt": excerpts.get(term, "")}
        for term, count in counts.most_common()
    ]
    items.sort(key=lambda item: (-int(item["count"]), str(item["term"])))
    return items[:limit]


def build_chapter_analysis(text: str) -> dict[str, Any]:
    cleaned = clean_text(text)
    return {
        "digest": text_digest(cleaned),
        "summary": summarize_text(cleaned),
        "characters": extract_characters(cleaned),
        "terms": extract_terms(cleaned),
    }


def build_recap(chapters: Iterable[dict[str, Any]], *, current_number: int, limit: int = 6) -> list[dict[str, Any]]:
    eligible = [chapter for chapter in chapters if int(chapter.get("number") or 0) < int(current_number)]
    eligible = sorted(eligible, key=lambda item: int(item.get("number") or 0))[-max(1, limit):]
    result: list[dict[str, Any]] = []
    for chapter in eligible:
        summary = str(chapter.get("summary") or summarize_text(str(chapter.get("text") or ""), max_sentences=2, max_chars=520)).strip()
        if not summary:
            continue
        result.append({
            "chapter_id": int(chapter.get("id") or 0),
            "chapter_number": int(chapter.get("number") or 0),
            "chapter_title": str(chapter.get("title") or f"Глава {chapter.get('number') or ''}").strip(),
            "summary": summary,
        })
    return result


def _intent_subject(question: str) -> str:
    normalized = re.sub(r"\s+", " ", str(question or "").strip())
    patterns = (
        r"^(?:кто\s+(?:такой|такая|такие)|кто\s+это)\s+(.+?)[?.!]*$",
        r"^(?:что\s+(?:такое|за)|объясни)\s+(.+?)[?.!]*$",
        r"^(?:расскажи|напомни)\s+(?:мне\s+)?про\s+(.+?)[?.!]*$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" ?.!")[:80]
    return ""


def answer_question(question: str, chapters: Iterable[dict[str, Any]], *, current_number: int) -> dict[str, Any]:
    clean_question = re.sub(r"\s+", " ", str(question or "").strip())[:400]
    chapter_rows = [
        chapter for chapter in chapters
        if 0 < int(chapter.get("number") or 0) <= int(current_number)
    ]
    chapter_rows.sort(key=lambda item: int(item.get("number") or 0))
    lowered = clean_question.lower().replace("ё", "е")

    if any(phrase in lowered for phrase in ("что было", "напомни события", "кратко до", "ранее произошло", "до этой главы")):
        recap = build_recap(chapter_rows, current_number=current_number, limit=6)
        if not recap:
            return {
                "answer": "До этой главы в доступном вам тексте пока недостаточно событий для краткого напоминания.",
                "sources": [],
                "confidence": "low",
                "spoiler_limit": int(current_number),
            }
        answer = "\n\n".join(
            f"Глава {item['chapter_number']} «{item['chapter_title']}»: {item['summary']}" for item in recap
        )
        return {
            "answer": answer,
            "sources": [
                {"chapter_id": item["chapter_id"], "chapter_number": item["chapter_number"], "chapter_title": item["chapter_title"]}
                for item in recap
            ],
            "confidence": "high",
            "spoiler_limit": int(current_number),
        }

    subject = _intent_subject(clean_question)
    keywords = question_keywords(subject or clean_question, limit=10)
    if subject:
        subject_norm = subject.lower().replace("ё", "е")
        for part in _tokens(subject):
            if part not in keywords and part not in _STOPWORDS:
                keywords.insert(0, part)
        keywords = keywords[:10]
    if not keywords:
        return {
            "answer": "Сформулируйте вопрос точнее: укажите имя персонажа, термин или событие.",
            "sources": [],
            "confidence": "low",
            "spoiler_limit": int(current_number),
        }

    matches: list[tuple[float, int, dict[str, Any], str]] = []
    for chapter in chapter_rows:
        for sentence_index, sentence in enumerate(split_sentences(str(chapter.get("text") or ""))):
            normalized = sentence.lower().replace("ё", "е")
            overlap = sum(1 for keyword in keywords if keyword in normalized)
            if overlap <= 0:
                continue
            exact_bonus = 3.5 if subject and subject_norm in normalized else 0.0
            density = overlap / max(1, len(keywords))
            recency = int(chapter.get("number") or 0) / max(1, int(current_number)) * 0.35
            sentence_bonus = 0.25 if 35 <= len(sentence) <= 300 else 0.0
            score = overlap * 1.5 + density + exact_bonus + recency + sentence_bonus - sentence_index * 0.0005
            matches.append((score, int(chapter.get("number") or 0), chapter, sentence))

    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    chosen: list[tuple[dict[str, Any], str]] = []
    seen: set[str] = set()
    for _, _, chapter, sentence in matches:
        key = re.sub(r"\W+", "", sentence.lower())[:180]
        if not key or key in seen:
            continue
        seen.add(key)
        chosen.append((chapter, sentence))
        if len(chosen) >= 4:
            break

    if not chosen:
        return {
            "answer": "В доступных вам главах до текущей включительно я не нашёл надёжного ответа. Возможно, имя или термин написаны иначе.",
            "sources": [],
            "confidence": "low",
            "spoiler_limit": int(current_number),
        }

    answer_parts: list[str] = []
    sources: list[dict[str, Any]] = []
    for chapter, sentence in sorted(chosen, key=lambda item: int(item[0].get("number") or 0)):
        number = int(chapter.get("number") or 0)
        title = str(chapter.get("title") or f"Глава {number}")
        answer_parts.append(f"Глава {number} «{title}»: {sentence}")
        source = {"chapter_id": int(chapter.get("id") or 0), "chapter_number": number, "chapter_title": title}
        if source not in sources:
            sources.append(source)

    confidence = "high" if subject and any(subject.lower().replace("ё", "е") in part.lower().replace("ё", "е") for part in answer_parts) else ("medium" if len(chosen) >= 2 else "low")
    return {
        "answer": "\n\n".join(answer_parts),
        "sources": sources,
        "confidence": confidence,
        "spoiler_limit": int(current_number),
    }
