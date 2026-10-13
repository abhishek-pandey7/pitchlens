"""Test-wide settings."""

import os

# No background tracking builds during tests: they would compete with the tests for CPU.
os.environ.setdefault("PITCHLENS_WARM_TRACKING", "0")
