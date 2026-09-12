"""Locate the existing engine without depending on the shell's working directory."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents/skills/story-bible-studio/scripts"
sys.path.insert(0, str(SCRIPTS))
