"""Privacy filtering shared by recording and note writes."""
import re

_PRIVATE = re.compile(r"<private>.*?(?:</private>|\Z)", re.IGNORECASE | re.DOTALL)
_SECRETS = [re.compile(pattern, flags) for pattern, flags in (
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL),
    (r"\b(?:sk|pk|rk)-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_\-]{20,}", 0),
    (r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}", 0),
    (r"\bAKIA[0-9A-Z]{16}\b", 0),
    (r"\bxox[abposr]-[A-Za-z0-9\-]{10,}", 0),
    (r"\bAIza[0-9A-Za-z_\-]{35}\b", 0),
    (r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", 0),
    (r"(?i)\b(?:bearer)\s+[A-Za-z0-9._\-]{20,}", 0),
    (r"(?i)\b((?:api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*)(\"[^\"]{6,}\"|'[^']{6,}'|\S{6,})", 0),
)]
SECRET_MARK = "[secret removed]"
PRIVATE_MARK = "[private]"


def scrub(text: str) -> str:
    """Remove text the user marked private and values that look like credentials."""
    text = _PRIVATE.sub(PRIVATE_MARK, text)
    for pattern in _SECRETS:
        text = pattern.sub(lambda match: (match.group(1) + SECRET_MARK) if pattern.groups else SECRET_MARK, text)
    return text
