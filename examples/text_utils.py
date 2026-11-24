"""String helpers."""


def is_palindrome(text: str) -> bool:
    cleaned = "".join(ch.lower() for ch in text if ch.isalnum())
    return cleaned == cleaned[::-1]


def word_count(text: str) -> int:
    return len(text.split())


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def capitalize_words(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in text.split(" "))


def count_vowels(text: str) -> int:
    return sum(1 for ch in text.lower() if ch in "aeiou")
