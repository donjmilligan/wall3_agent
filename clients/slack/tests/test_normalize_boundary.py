from __future__ import annotations

import ast
from pathlib import Path

NORMALIZE_DIR = Path(__file__).parent.parent / "src" / "wall3_slack" / "normalize"

FORBIDDEN = (
    "requests",
    "urllib3",
    "http.client",
    "socket",
    "wall3_slack.client",
    "wall3_slack.extract",
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 2:
                # `from ..client import X` inside wall3_slack/normalize/ -> wall3_slack.client
                if node.module:
                    found.append(f"wall3_slack.{node.module}")
                else:
                    found.extend(f"wall3_slack.{alias.name}" for alias in node.names)
            elif node.module:
                found.append(node.module)
    return found


def _violations(root: Path) -> list[str]:
    bad: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for module in _imports(path):
            if module.startswith(FORBIDDEN):
                bad.append(f"{path.name}: {module}")
    return bad


def test_normalize_never_imports_the_network_or_extract():
    """normalize/ must be re-runnable offline against a fixed raw snapshot."""
    assert list(NORMALIZE_DIR.rglob("*.py")), "normalize/ not found -- the test would be vacuous"
    assert _violations(NORMALIZE_DIR) == []


def test_the_boundary_check_flags_each_forbidden_shape(tmp_path):
    """Paired positive: the checker must flag planted violations, or the test above is vacuous."""
    (tmp_path / "bad_relative.py").write_text("from ..client import SlackClient\n")
    (tmp_path / "bad_bare.py").write_text("from .. import extract\n")
    (tmp_path / "bad_absolute.py").write_text("import requests\n")
    (tmp_path / "fine.py").write_text("from ..models import Message\nfrom .fields import ts_key\n")

    assert _violations(tmp_path) == [
        "bad_absolute.py: requests",
        "bad_bare.py: wall3_slack.extract",
        "bad_relative.py: wall3_slack.client",
    ]
