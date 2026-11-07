MAX_REASON_LENGTH = 1000


def clean_reason(raw) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise ValueError("A reason is required.")
    if len(text) > MAX_REASON_LENGTH:
        raise ValueError(f"Reason must be at most {MAX_REASON_LENGTH} characters.")
    return text
