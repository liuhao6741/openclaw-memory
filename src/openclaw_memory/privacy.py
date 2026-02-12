"""Privacy filter: regex-based sensitive information detection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PrivacyFilter:
    """Detects and optionally redacts sensitive information using regex patterns."""

    patterns: list[str] = field(default_factory=list)
    enabled: bool = True
    _compiled: list[re.Pattern[str]] = field(default_factory=list, repr=False, init=False)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.patterns]

    def contains_sensitive(self, text: str) -> bool:
        """Return True if *text* contains any sensitive pattern."""
        if not self.enabled:
            return False
        return any(pat.search(text) for pat in self._compiled)

    def get_violations(self, text: str) -> list[str]:
        """Return list of matched pattern strings found in *text*."""
        if not self.enabled:
            return []
        violations: list[str] = []
        for pat in self._compiled:
            if pat.search(text):
                violations.append(pat.pattern)
        return violations

    def redact(self, text: str) -> str:
        """Replace sensitive matches with ``[REDACTED]``."""
        if not self.enabled:
            return text
        result = text
        for pat in self._compiled:
            result = pat.sub("[REDACTED]", result)
        return result
