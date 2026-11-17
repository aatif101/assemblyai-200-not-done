import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# A fake key for the whole test session. Tests assert it never reaches disk or stdout.
FAKE_KEY = "0123456789abcdef" * 2   # built at runtime so no key-shaped literal is committed
os.environ["ASSEMBLYAI_API_KEY"] = FAKE_KEY
