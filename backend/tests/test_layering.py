"""Layering guard: vigilus.core must not import from vigilus.api.

api/ is the HTTP layer; core/ is the runtime shared by web chat, the
scheduler and the channel gateway. A core → api import means shared turn
logic has crept back into a route module (issue #59).
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "vigilus" / "core"
VIGILUS = CORE.parent


def _is_api(module: str | None) -> bool:
    return module is not None and (module == "vigilus.api" or module.startswith("vigilus.api."))


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    """Absolute dotted module a relative ``ImportFrom`` resolves to.

    Mirrors ``importlib._bootstrap._resolve_name``: drop the trailing
    ``level - 1`` components of *package*, then append *module* (if any).
    E.g. package "vigilus.core", level 2, module "api" -> "vigilus.api".
    """
    bits = package.rsplit(".", level - 1)
    base = bits[0]
    return f"{base}.{module}" if module else base


def _flagged_imports(tree: ast.AST, filename: str, package: str) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            resolved = _resolve_relative(package, node.level, node.module) if node.level else node.module
            if _is_api(resolved):
                dots = "." * node.level
                hits.append(f"{filename}:{node.lineno} from {dots}{node.module or ''}")
        elif isinstance(node, ast.Import):
            hits += [
                f"{filename}:{node.lineno} import {a.name}" for a in node.names if _is_api(a.name)
            ]
    return hits


def _package_name(path: Path) -> str:
    """The dotted package a module at *path* (under vigilus/) belongs to.

    Used to resolve that module's relative imports the way Python would,
    via its ``__package__``.
    """
    rel = path.relative_to(VIGILUS.parent).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    module = ".".join(parts)
    return module if path.name == "__init__.py" else module.rsplit(".", 1)[0]


def _api_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return _flagged_imports(tree, path.name, _package_name(path))


def test_core_does_not_import_api():
    offenders = [hit for path in sorted(CORE.rglob("*.py")) for hit in _api_imports(path)]
    assert offenders == []


def test_relative_import_from_core_into_api_is_flagged():
    """A `from ..api import x` inside a vigilus/core/*.py file resolves to
    vigilus.api and must be flagged, the same as an absolute import."""
    tree = ast.parse("from ..api import chat\n")
    hits = _flagged_imports(tree, "turn.py", package="vigilus.core")
    assert hits == ["turn.py:1 from ..api"]
