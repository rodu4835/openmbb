"""Canonical PII-shape detectors.

The definitions moved into the package (openmbb/redact.py) so the release gate,
the fixture guards and the share-safe export all harden together — an exporter
that used a weaker copy of these shapes would be the worst place for them to
drift. This module stays as the import the tests were written against.
"""

from openmbb.redact import (  # noqa: F401
    MBB_SERIAL_SHAPE,
    MODULE_SERIAL_SHAPE,
    PLACEHOLDERS,
    SEVCON_SERIAL_SHAPE,
    SHAPES,
    VIN_SHAPE,
    find_pii_shapes,
)
