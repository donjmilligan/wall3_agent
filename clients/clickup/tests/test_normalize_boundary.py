from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
NORMALIZE_DIR = SRC / "wall3_clickup" / "normalize"

FORBIDDEN = (
    "requests",
    "urllib",
    "http",
    "socket",
    "dotenv",
    "wall3_clickup.client",
    "wall3_clickup.extract",
    "wall3_clickup.config",
    "wall3_clickup.cli",
)


def _imported_modules(path: Path) -> list[str]:
    """Absolute names of every import in `path`, resolving relative imports.

    `from ..client import X` has `module == "client"` and `level == 2`; a
    prefix check on the bare name would miss it, which is exactly the form a
    sibling-package import takes.
    """
    package_parts = list(path.relative_to(SRC).with_suffix("").parts[:-1])
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                prefix = ".".join(base)
                if node.module:
                    modules.append(f"{prefix}.{node.module}")
                else:
                    modules.extend(f"{prefix}.{alias.name}" for alias in node.names)
            elif node.module:
                modules.append(node.module)
    return modules


def _is_forbidden(module: str) -> bool:
    return any(module == f or module.startswith(f + ".") for f in FORBIDDEN)


def test_normalize_never_imports_network_client_extract_or_config():
    files = sorted(NORMALIZE_DIR.rglob("*.py"))
    assert len(files) >= 3, "boundary test found too few files -- is the path right?"

    violations = [
        f"{path.name}: imports {module!r}"
        for path in files
        for module in _imported_modules(path)
        if _is_forbidden(module)
    ]

    assert not violations, f"normalize/ must stay offline: {violations}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from ..client import ClickUpClient\n", "wall3_clickup.client"),
        ("from .. import extract\n", "wall3_clickup.extract"),
        ("import requests\n", "requests"),
        ("from wall3_clickup.config import raw_cache_root\n", "wall3_clickup.config"),
    ],
)
def test_the_scanner_catches_each_import_form(tmp_path, monkeypatch, source, expected):
    """The checker itself must see relative and absolute forms, or the test above is vacuous."""
    fake = SRC / "wall3_clickup" / "normalize" / "_probe_does_not_exist.py"
    monkeypatch.setattr(Path, "read_text", lambda self, encoding=None: source)

    modules = _imported_modules(fake)

    assert expected in modules
    assert _is_forbidden(expected)


def test_models_import_is_allowed():
    assert not _is_forbidden("wall3_clickup.models")
