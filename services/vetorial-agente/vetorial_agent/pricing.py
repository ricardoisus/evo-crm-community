"""Versioned reference rates; not provider-confirmed billing.

Usage remains unknown until an authoritative reconciliation supplies complete usage,
tier, cache writes, context and duration. Never reprice historical calls implicitly.
"""
import json
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name("pricing.json").read_text())
VERSION = CATALOG["version"]
