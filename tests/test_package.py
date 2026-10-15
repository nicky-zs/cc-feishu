import subprocess
import sys
from pathlib import Path

import cc_feishu


def test_version() -> None:
    assert cc_feishu.__version__ == "0.1.0"


def test_python_m_help_exits_zero() -> None:
    repo = Path(__file__).resolve().parents[1]
    p = subprocess.run(
        [sys.executable, "-m", "cc_feishu", "--help"],
        capture_output=True,
        text=True,
        cwd=repo,
        env={"PYTHONPATH": str(repo / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert p.returncode == 0
    assert "cc-feishu" in p.stdout
