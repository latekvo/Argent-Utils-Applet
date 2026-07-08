"""Shared pytest setup: import path + QSettings isolation.

Every test that builds a Store must never touch the user's real QSettings
(a user who e.g. hid tools would otherwise change test outcomes — and tests
would scribble on their live config). Redirect all QSettings to a per-test
temp dir before anything constructs one.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QSettings  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_qsettings(tmp_path):
    """Point QSettings at a fresh temp dir for the duration of each test."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path)
    )
    yield
