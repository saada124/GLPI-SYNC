import pytest
from lookup import LookupMap


class TestLookupMap:
    def test_add_and_resolve(self):
        m = LookupMap()
        m.add("Helpdesk", 1)
        m.add("Network", 2)
        assert m.resolve("Helpdesk") == 1
        assert m.resolve("Network") == 2

    def test_resolve_is_case_sensitive(self):
        m = LookupMap()
        m.add("Helpdesk", 1)
        assert m.resolve("helpdesk") is None

    def test_resolve_strips_input(self):
        m = LookupMap()
        m.add("Helpdesk", 1)
        assert m.resolve("  Helpdesk  ") == 1

    def test_resolve_missing_returns_none(self):
        m = LookupMap()
        assert m.resolve("Anything") is None

    def test_resolve_reverse(self):
        m = LookupMap()
        m.add("Helpdesk", 1)
        assert m.resolve_reverse(1) == "Helpdesk"

    def test_resolve_reverse_missing_returns_none(self):
        m = LookupMap()
        assert m.resolve_reverse(999) is None

    def test_round_trip(self):
        m = LookupMap()
        m.add("Sage X3", 26)
        resolved_id = m.resolve("Sage X3")
        assert resolved_id == 26
        assert m.resolve_reverse(resolved_id) == "Sage X3"

    def test_resolve_reverse_with_int(self):
        m = LookupMap()
        m.add("Item", 42)
        assert m.resolve("Item") == 42
        assert m.resolve_reverse(42) == "Item"
