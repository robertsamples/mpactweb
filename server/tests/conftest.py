"""Test-wide settings."""

import os

# Parsing the whole atlas at app startup is for a long-lived server; in tests
# it only competes for CPU with the test itself.
os.environ.setdefault('MPACT_PREWARM', '0')
