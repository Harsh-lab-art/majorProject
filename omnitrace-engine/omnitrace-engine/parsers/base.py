"""
Abstract interface every OEM parser plugin must implement.

A parser's ONLY responsibility is: given a raw disk image, return a
list of ClipEntry objects (both live and recoverable-deleted). It must
NOT do its own hashing or report writing — that belongs to hasher.py
and report.py and happens identically regardless of OEM.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from models import ParseResult


class BaseParser(ABC):
    name: str = "unknown"
    confidence: str = "heuristic"
    # Whether the shared raw carver should also scan bytes this parser did not claim.
    carve_uncovered: bool = True

    @abstractmethod
    def sniff(self, header: bytes) -> bool:
        """Cheap check on the first chunk of the image. Used by the OEM detector."""
        raise NotImplementedError

    @abstractmethod
    def parse(self, image_path: str) -> ParseResult:
        """
        Full parse of the image. Must return every clip it can validate,
        plus stats about what it rejected. This is
        where the OEM-specific file-system/container knowledge lives.
        """
        raise NotImplementedError
