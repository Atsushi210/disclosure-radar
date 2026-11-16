"""テスト共通: 一時ディレクトリの SQLite を使い data/radar.db を汚さない。"""
from __future__ import annotations

from pathlib import Path

import pytest

import radar.db as db_module

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module.init_db(path)
    return path


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")
