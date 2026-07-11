import json
import pytest
from cache import StateCache, CACHE_DIR


class TestStateCache:
    def test_init_creates_dir(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr("cache.CACHE_DIR", cache_dir)
        assert not cache_dir.exists()
        sc = StateCache()
        assert cache_dir.exists()

    def test_get_last_sync_default(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr("cache.CACHE_DIR", cache_dir)
        sc = StateCache()
        assert sc.get_last_sync() == "1970-01-01T00:00:00"

    def test_set_and_get_last_sync(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr("cache.CACHE_DIR", cache_dir)
        sc = StateCache()
        sc.set_last_sync("2026-07-11T12:00:00")
        assert sc.get_last_sync() == "2026-07-11T12:00:00"

    def test_persistence_across_instances(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr("cache.CACHE_DIR", cache_dir)
        StateCache().set_last_sync("2026-07-11T12:00:00")
        sc2 = StateCache()
        assert sc2.get_last_sync() == "2026-07-11T12:00:00"

    def test_corrupted_json_returns_default(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr("cache.CACHE_DIR", cache_dir)
        cache_dir.mkdir(exist_ok=True)
        (cache_dir / "state.json").write_text("not valid json", encoding="utf-8")
        sc = StateCache()
        assert sc.get_last_sync() == "1970-01-01T00:00:00"

    def test_default_str_for_non_serializable(self, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr("cache.CACHE_DIR", cache_dir)
        sc = StateCache()
        sc._data["path"] = object()
        sc._save()
        loaded = json.loads((cache_dir / "state.json").read_text(encoding="utf-8"))
        assert loaded["path"].startswith("<")
