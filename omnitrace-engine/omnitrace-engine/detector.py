"""
OEM Detector — Section 7.6 of the design doc.

This module has exactly one job: look at the first chunk of a raw
disk image and decide which parser plugin should handle it. It knows
NOTHING about file systems, codecs, or recovery — that's the whole
point of the plugin boundary.

Detection strategy (documented honestly, not oversold):
  - Dahua images are detected with high confidence: Dahua's DHAV
    container format starts every video chunk with the ASCII magic
    b"DHAV", and a disk/image produced by a Dahua DVR will contain
    many of these in the first few MB. This is a real, well-documented
    signature (see README "References").
  - Hikvision images are detected with a weaker heuristic: Hikvision
    system partitions commonly contain the plaintext ASCII string
    b"HIKVISION" in their system/header area. This is NOT a formally
    documented magic number the way DHAV is — treat it as a starting
    point, and tighten it once Manish hands over a real Hikvision
    image (Section 7.7 of the design doc covers that handoff).

Add a new OEM by writing a new parser module with a `PLUGIN` object
exposing `name` and `sniff(header: bytes) -> bool`, then registering
it in PARSER_REGISTRY below. Nothing else in the pipeline changes —
that's the guarantee the plugin architecture is supposed to give us.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Protocol

from models import UnsupportedOEMError

logger = logging.getLogger("omnitrace.detector")

# How many bytes of the image we read to make the detection decision.
# DHAV magics tend to show up well within the first few MB on a
# freshly-recorded Dahua disk; this is deliberately generous so we
# don't miss a signature that starts a little further in.
SNIFF_WINDOW_BYTES = 8 * 1024 * 1024  # 8 MB


class ParserPlugin(Protocol):
    name: str

    def sniff(self, header: bytes) -> bool:
        """Return True if this plugin recognizes the image."""
        ...


@dataclass
class DetectionResult:
    oem: str
    confidence: str  # "high" | "heuristic" — surfaced to the UI/report, never hidden


def detect_oem(image_path: str, registry: List[ParserPlugin]) -> DetectionResult:
    """
    Read the sniff window from the image and ask each registered
    plugin, in order, whether it recognizes the data. First match wins.
    Order plugins with the most reliable signature first.
    """
    with open(image_path, "rb") as f:
        header = f.read(SNIFF_WINDOW_BYTES)

    if not header:
        raise UnsupportedOEMError(f"Image at {image_path} is empty or unreadable")

    for plugin in registry:
        try:
            if plugin.sniff(header):
                confidence = getattr(plugin, "confidence", "heuristic")
                logger.info("OEM detected: %s (confidence=%s)", plugin.name, confidence)
                return DetectionResult(oem=plugin.name, confidence=confidence)
        except Exception as exc:  # a broken plugin should never crash detection
            logger.warning("Plugin %s raised during sniff(): %s", getattr(plugin, "name", "?"), exc)
            continue

    raise UnsupportedOEMError(
        f"No registered parser plugin recognized the image at {image_path}. "
        f"Registered plugins: {[p.name for p in registry]}"
    )


def build_default_registry() -> List[ParserPlugin]:
    """
    The single place that knows about every supported OEM.
    Month 1: Hikvision + Dahua. Month 2: add CP Plus here and nowhere else.
    """
    from parsers.dahua import PLUGIN as dahua_plugin
    from parsers.generic import PLUGIN as generic_plugin
    from parsers.hikvision import PLUGIN as hikvision_plugin

    # Dahua first: its signature is the more reliable one, so give it
    # first refusal before falling back to Hikvision's weaker heuristic.
    # Generic LAST: it only claims images no vendor plugin recognised (carving-only fallback).
    return [dahua_plugin, hikvision_plugin, generic_plugin]
