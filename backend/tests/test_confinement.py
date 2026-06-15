"""Tests for working_dir confinement and JIT resource scoping."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from vigilus.core.rbac import _resource_covers
from vigilus.tools.native.host import _confine, fs_read, fs_write, shell_exec


def _operator(working_dir: str | None):
    return SimpleNamespace(working_dir=working_dir, id="op-test", name="Test Op")


class TestConfine:
    def test_unrestricted_without_working_dir(self):
        path, err = _confine("/etc/passwd", _operator(None))
        assert err is None
        assert path == "/etc/passwd"

    def test_relative_path_resolves_inside(self, tmp_path):
        op = _operator(str(tmp_path))
        path, err = _confine("notes.txt", op)
        assert err is None
        assert path == os.path.join(os.path.realpath(str(tmp_path)), "notes.txt")

    def test_absolute_path_inside_allowed(self, tmp_path):
        op = _operator(str(tmp_path))
        target = tmp_path / "sub" / "file.txt"
        path, err = _confine(str(target), op)
        assert err is None

    def test_absolute_path_outside_denied(self, tmp_path):
        op = _operator(str(tmp_path))
        path, err = _confine("/etc/passwd", op)
        assert path is None
        assert "outside" in err

    def test_traversal_denied(self, tmp_path):
        op = _operator(str(tmp_path))
        path, err = _confine("../../../etc/passwd", op)
        assert path is None
        assert err is not None

    def test_sibling_prefix_dir_denied(self, tmp_path):
        """/data must not authorize /data-evil (the old startswith bug)."""
        base = tmp_path / "data"
        base.mkdir()
        evil = tmp_path / "data-evil"
        evil.mkdir()
        op = _operator(str(base))
        path, err = _confine(str(evil / "x.txt"), op)
        assert path is None

    def test_symlink_escape_denied(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        os.symlink(outside, base / "link")
        op = _operator(str(base))
        path, err = _confine(str(base / "link" / "secret.txt"), op)
        assert path is None


class TestHostToolsConfinement:
    @pytest.mark.asyncio
    async def test_fs_read_outside_denied(self, tmp_path):
        op = _operator(str(tmp_path))
        result = await fs_read({"path": "/etc/passwd"}, operator=op)
        assert "outside" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_fs_write_then_read_inside(self, tmp_path):
        op = _operator(str(tmp_path))
        result = await fs_write({"path": "out.txt", "content": "hello"}, operator=op)
        assert result.get("status") == "success"
        result = await fs_read({"path": "out.txt"}, operator=op)
        assert result.get("content") == "hello"

    @pytest.mark.asyncio
    async def test_shell_exec_cwd_confined(self, tmp_path):
        op = _operator(str(tmp_path))
        result = await shell_exec({"command": "pwd", "working_dir": "/etc"}, operator=op)
        assert "outside" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_shell_exec_runs_in_working_dir(self, tmp_path):
        op = _operator(str(tmp_path))
        result = await shell_exec({"command": "pwd"}, operator=op)
        assert result.get("exit_code") == 0
        assert os.path.realpath(str(tmp_path)) in result.get("stdout", "")


class TestJitResourceCovers:
    def test_wildcard_covers_everything(self):
        assert _resource_covers("*", "/anything/at/all")

    def test_exact_match(self):
        assert _resource_covers("/etc/nginx", "/etc/nginx")

    def test_path_containment(self):
        assert _resource_covers("/etc/nginx", "/etc/nginx/nginx.conf")

    def test_glob_pattern(self):
        assert _resource_covers("/var/log/*.log", "/var/log/auth.log")

    def test_unrelated_path_denied(self):
        assert not _resource_covers("/etc/nginx", "/etc/shadow")

    def test_sibling_prefix_denied(self):
        assert not _resource_covers("/data", "/data-evil/file")
