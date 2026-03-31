from __future__ import annotations

import re


BAD_EXACT_VALUES = {
    "",
    "-",
    "--",
    ".",
    "..",
    "/",
    "na",
    "n/a",
    "none",
    "null",
    "ok",
    "co",
}


def normalize_address_value(value) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def is_probably_valid_address(value) -> bool:
    text = normalize_address_value(value)
    if not text:
        return False

    low = text.lower()
    if low in BAD_EXACT_VALUES:
        return False

    # Trop court pour une adresse exploitable
    if len(text) < 5:
        return False

    # Une seule lettre ou deux lettres isolées
    if re.fullmatch(r"[A-Za-z]{1,2}", text):
        return False

    return True


def clean_address_or_empty(value) -> str:
    text = normalize_address_value(value)
    return text if is_probably_valid_address(text) else ""
