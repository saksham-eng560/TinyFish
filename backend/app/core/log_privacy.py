"""Keep personal data out of logs (docs/HIREFLOW_PLAN.md §5.3): e-mail addresses, phone numbers and
secrets (bearer tokens, JWTs, API keys, ?token= parameters, cookies) are masked before a line is written."""

from __future__ import annotations

import logging
import re

_EMAIL = re.compile(r"([A-Za-z0-9])[A-Za-z0-9._%+-]*@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
# International numbers (+91 98765 43210, +1 (415) 555-0100) and bare 10-digit mobiles; never dates or ids
_PHONE_INTL = re.compile(r"\+\d{1,3}[\s.-]?\(?\d{1,5}\)?(?:[\s.-]?\d{2,5}){2,4}")
_PHONE_BARE = re.compile(r"(?<![\w.:/-])\d{10}(?![\w.:/-])")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_API_KEY = re.compile(r"\b(sk|pk|rk|xox[abp])-[A-Za-z0-9_-]{10,}")
# In URLs (?token=..., &code=...) and as "name: value" / "name=value" for names that are always secret
_SECRET_QUERY = re.compile(r"(?i)([?&;](?:token|access_token|refresh_token|api_key|apikey|key|password|secret|code|session|sig)=)"
                           r"[^\s&\"'#]+")
_SECRET_FIELD = re.compile(r"(?i)\b(password|passwd|secret|api_key|apikey|access_token|refresh_token|set-cookie|cookie)"
                           r"(\s*[:=]\s*)[^\s,;\"']{4,}")


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"***{digits[-2:]}" if len(digits) >= 8 else match.group(0)


def mask_pii(text: str) -> str:
    if not text:
        return text
    text = _JWT.sub("[jwt]", text)
    text = _BEARER.sub(r"\1 [redacted]", text)
    text = _API_KEY.sub(r"\1-[redacted]", text)
    text = _SECRET_QUERY.sub(r"\1[redacted]", text)
    text = _SECRET_FIELD.sub(r"\1\2[redacted]", text)
    text = _EMAIL.sub(r"\1***@\2", text)
    text = _PHONE_INTL.sub(_mask_phone, text)
    return _PHONE_BARE.sub(_mask_phone, text)


class PIIFilter(logging.Filter):
    """Masks the formatted message (and any exception text) of every record that passes through."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string: leave the record as it is
            return True
        masked = mask_pii(message)
        if masked != message:
            record.msg, record.args = masked, ()
        if record.exc_info and not record.exc_text:
            record.exc_text = mask_pii(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = mask_pii(record.exc_text)
        return True


def install(*logger_names: str) -> None:
    """Attach the filter to the root handlers and to the named loggers (e.g. uvicorn's, which don't propagate)."""
    pii = PIIFilter()
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, PIIFilter) for f in handler.filters):
            handler.addFilter(pii)
    for name in logger_names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, PIIFilter) for f in logger.filters):
            logger.addFilter(pii)
        for handler in logger.handlers:
            if not any(isinstance(f, PIIFilter) for f in handler.filters):
                handler.addFilter(pii)
