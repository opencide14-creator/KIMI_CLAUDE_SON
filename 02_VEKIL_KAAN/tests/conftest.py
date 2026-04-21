"""tests/conftest.py — pytest configuration."""
import sys
from pathlib import Path

# Project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))
