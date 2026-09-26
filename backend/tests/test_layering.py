"""Layering guard: vigilus.core must not import from vigilus.api.

api/ is the HTTP layer; core/ is the runtime shared by web chat, the
scheduler and the channel gateway. A core → api import means shared turn
logic has crept back into a route module (issue #59).
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "vigilus" / "core"


def _is_api(module: str | None) -> bool:
    return module is not None and (module == "vigilus.api" or module.startswith("vigilus.api."))


def _api_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _is_api(node.module):
            hits.append(f"{path.name}:{node.lineno} from {node.module}")
        elif isinstance(node, ast.Import):
            hits += [
                f"{path.name}:{node.lineno} import {a.name}" for a in node.names if _is_api(a.name)
            ]
    return hits


def test_core_does_not_import_api():
    offenders = [hit for path in sorted(CORE.rglob("*.py")) for hit in _api_imports(path)]
    assert offenders == []
