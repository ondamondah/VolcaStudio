"""Check that the windowed executable returns both worker output streams."""
from pathlib import Path
import subprocess
import sys

exe = Path(sys.argv[1]).resolve()
result = subprocess.run(
    [str(exe), "--device-worker",
     "import sys; print(sys.argv[1]); print('worker stderr', file=sys.stderr)",
     "worker stdout"],
    capture_output=True, text=True, check=True, timeout=30,
)
assert result.stdout.strip() == "worker stdout", result.stdout
assert result.stderr.strip() == "worker stderr", result.stderr
