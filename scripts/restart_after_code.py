"""Detached restart after a reviewed Telegram patch has been acknowledged."""
import subprocess
import sys
import time
from pathlib import Path


root = Path(__file__).resolve().parent.parent
time.sleep(3)
subprocess.run([sys.executable, '-m', 'app.manage', 'restart'], cwd=root,
               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, timeout=90, check=False)
