from pathlib import Path
import pytest
from field_mappings import EntityMapping, load_mappings, MAPPINGS_PATH


class TestEntityMapping:
    def test_basic_construction(self):
        config = {
            "sheet_tab": "Users",
            "glpi_itemtype": "User",
            "api_endpoint": "User",
            "fields": {"Name": "name"},
            "code_lookups": {},
            "lookups": {},
            "constants": {},
            "helper_columns": {"glpi_id": "GLPI_ID", "synced_at": "Synced_At"},
        }
        m = EntityMapping("Users", config)
        assert m.name == "Users"
        assert m.sheet_tab == "Users"
        assert m.glpi_itemtype == "User"
        assert m.api_endpoint == "User"
        assert m.id_field == "id"
        assert m.glpi_id_col == "GLPI_ID"
        assert m.synced_at_col == "Synced_At"
        assert m.modified_at_col is None

    def test_sheet_to_glpi_simple(self):
        config = {"sheet_tab": "Tickets", "glpi_itemtype": "Ticket", "api_endpoint": "Ticket",
                   "fields": {"Title": "name", "Description": "content"},
                   "code_lookups": {}, "lookups": {}, "constants": {}}
        m = EntityMapping("Tickets", config)
        result = m.sheet_to_glpi({"Title": "Hello", "Description": "World"})
        assert result == {"name": "Hello", "content": "World"}

    def test_sheet_to_glpi_code_lookup(self):
        config = {"sheet_tab": "Users", "glpi_itemtype": "User", "api_endpoint": "User",
                   "fields": {"Role": "profiles_id"},
                   "code_lookups": {"Role": {"user": 1, "admin": 3}},
                   "lookups": {}, "constants": {}}
        m = EntityMapping("Users", config)
        assert m.sheet_to_glpi({"Role": "user"}) == {"profiles_id": 1}
        assert m.sheet_to_glpi({"Role": "admin"}) == {"profiles_id": 3}

    def test_sheet_to_glpi_code_lookup_unknown_passes_through(self):
        config = {"sheet_tab": "Users", "glpi_itemtype": "User", "api_endpoint": "User",
                   "fields": {"Role": "profiles_id"},
                   "code_lookups": {"Role": {"user": 1}},
                   "lookups": {}, "constants": {}}
        m = EntityMapping("Users", config)
        assert m.sheet_to_glpi({"Role": "super_admin"}) == {"profiles_id": "super_admin"}

    def test_sheet_to_glpi_code_lookup_strips_whitespace(self):
        config = {"sheet_tab": "Users", "glpi_itemtype": "User", "api_endpoint": "User",
                   "fields": {"Active": "is_active"},
                   "code_lookups": {"Active": {"TRUE": 1, "FALSE": 0}},
                   "lookups": {}, "constants": {}}
        m = EntityMapping("Users", config)
        assert m.sheet_to_glpi({"Active": "  TRUE  "}) == {"is_active": 1}

    def test_sheet_to_glpi_filters_empty_values(self):
        config = {"sheet_tab": "Tickets", "glpi_itemtype": "Ticket", "api_endpoint": "Ticket",
                   "fields": {"Title": "name", "Notes": "content"},
                   "code_lookups": {}, "lookups": {}, "constants": {}}
        m = EntityMapping("Tickets", config)
        result = m.sheet_to_glpi({"Title": "Hello", "Notes": ""})
        assert result == {"name": "Hello"}

    def test_sheet_to_glpi_skips_null_glpi_field(self):
        config = {"sheet_tab": "Tickets", "glpi_itemtype": "Ticket", "api_endpoint": "Ticket",
                   "fields": {"Title": "name", "Ignore": ""},
                   "code_lookups": {}, "lookups": {}, "constants": {}}
        m = EntityMapping("Tickets", config)
        result = m.sheet_to_glpi({"Title": "Hello", "Ignore": "should not appear"})
        assert result == {"name": "Hello"}

    def test_sheet_to_glpi_includes_constants(self):
        config = {"sheet_tab": "ticket_assignments", "glpi_itemtype": "Ticket_User",
                   "api_endpoint": "Ticket_User",
                   "fields": {"Ticket_ID": "tickets_id"},
                   "code_lookups": {}, "lookups": {}, "constants": {"type": 2}}
        m = EntityMapping("ticket_assignments", config)
        result = m.sheet_to_glpi({"Ticket_ID": "42"})
        assert result == {"tickets_id": "42", "type": 2}


class TestRouting:
    def test_no_routing_by_default(self):
        config = {"sheet_tab": "Users", "glpi_itemtype": "User", "api_endpoint": "User",
                   "fields": {}, "code_lookups": {}, "lookups": {}, "constants": {}}
        m = EntityMapping("Users", config)
        assert m.routing_field is None
        assert m.get_route({"Category": "Laptop"}) is None

    def test_route_by_category(self):
        config = {"sheet_tab": "Assets", "glpi_itemtype": "Computer", "api_endpoint": "Computer",
                   "fields": {"Name": "name"}, "code_lookups": {}, "lookups": {}, "constants": {},
                   "helper_columns": {},
                   "itemtype_routing": {
                       "category_field": "Category",
                       "mapping": {
                           "Laptop": {"itemtype": "Computer", "endpoint": "Computer"},
                           "Écran": {"itemtype": "Monitor", "endpoint": "Monitor"},
                       }}}
        m = EntityMapping("Assets", config)
        assert m.get_route({"Category": "Laptop"}) == {"itemtype": "Computer", "endpoint": "Computer"}
        assert m.get_route({"Category": "Écran"}) == {"itemtype": "Monitor", "endpoint": "Monitor"}

    def test_route_unknown_returns_none(self):
        config = {"sheet_tab": "Assets", "glpi_itemtype": "Computer", "api_endpoint": "Computer",
                   "fields": {}, "code_lookups": {}, "lookups": {}, "constants": {},
                   "helper_columns": {},
                   "itemtype_routing": {
                       "category_field": "Category",
                       "mapping": {"Laptop": {"itemtype": "Computer", "endpoint": "Computer"}}}}
        m = EntityMapping("Assets", config)
        assert m.get_route({"Category": "UNKNOWN"}) is None

    def test_route_fallback_default(self):
        config = {"sheet_tab": "Assets", "glpi_itemtype": "Computer", "api_endpoint": "Computer",
                   "fields": {}, "code_lookups": {}, "lookups": {}, "constants": {},
                   "helper_columns": {},
                   "itemtype_routing": {
                       "category_field": "Category",
                       "default_itemtype": "Computer",
                       "default_endpoint": "Computer",
                       "mapping": {"Laptop": {"itemtype": "Computer", "endpoint": "Computer"}}}}
        m = EntityMapping("Assets", config)
        assert m.get_route({"Category": "UNKNOWN"}) == {"itemtype": "Computer", "endpoint": "Computer"}

    def test_route_fallback_to_other_category(self):
        config = {"sheet_tab": "Assets", "glpi_itemtype": "Computer", "api_endpoint": "Computer",
                   "fields": {}, "code_lookups": {}, "lookups": {}, "constants": {},
                   "helper_columns": {},
                   "itemtype_routing": {
                       "category_field": "Category",
                       "fallback_field": "Other_Category",
                       "mapping": {
                           "Cable": {"itemtype": "Cable", "endpoint": "Cable"},
                       }}}
        m = EntityMapping("Assets", config)
        assert m.get_route({"Category": "Autre", "Other_Category": "Cable"}) == {"itemtype": "Cable", "endpoint": "Cable"}

    def test_route_strips_whitespace(self):
        config = {"sheet_tab": "Assets", "glpi_itemtype": "Computer", "api_endpoint": "Computer",
                   "fields": {}, "code_lookups": {}, "lookups": {}, "constants": {},
                   "helper_columns": {},
                   "itemtype_routing": {
                       "category_field": "Category",
                       "mapping": {
                           "Laptop": {"itemtype": "Computer", "endpoint": "Computer"},
                       }}}
        m = EntityMapping("Assets", config)
        assert m.get_route({"Category": "  Laptop  "}) == {"itemtype": "Computer", "endpoint": "Computer"}


class TestLoadMappings:
    def test_load_mappings_from_fixture(self, monkeypatch, sample_yaml):
        monkeypatch.setattr("field_mappings.MAPPINGS_PATH", sample_yaml)
        mappings = load_mappings()
        assert "Users" in mappings
        assert "Tickets" in mappings
        assert "ticket_assignments" in mappings
        users = mappings["Users"]
        assert users.sheet_tab == "Users"
        assert users.glpi_itemtype == "User"
        assert users.code_lookups["Role"] == {"user": 1, "admin": 3, "super_admin": 4}
        assert users.fields["Name"] == "name"
        assert users.glpi_id_col == "GLPI_ID"

    def test_load_mappings_ticket_lookups(self, monkeypatch, sample_yaml):
        monkeypatch.setattr("field_mappings.MAPPINGS_PATH", sample_yaml)
        mappings = load_mappings()
        tickets = mappings["Tickets"]
        assert tickets.lookups["Category"] == "ITILCategory"
        assert tickets.code_lookups["Status"]["Nouveau"] == 1

    def test_load_mappings_constants(self, monkeypatch, sample_yaml):
        monkeypatch.setattr("field_mappings.MAPPINGS_PATH", sample_yaml)
        mappings = load_mappings()
        ta = mappings["ticket_assignments"]
        assert ta.constants == {"type": 2}

    def test_load_mappings_assets_routing(self, monkeypatch, sample_yaml):
        monkeypatch.setattr("field_mappings.MAPPINGS_PATH", sample_yaml)
        mappings = load_mappings()
        assets = mappings["Assets"]
        assert assets.routing_field == "Category"
        assert assets.routing_fallback == "Other_Category"
        assert assets.routing_default == "Computer"
        assert assets.routing_map["Laptop"] == {"itemtype": "Computer", "endpoint": "Computer"}
        assert assets.routing_map["Écran"] == {"itemtype": "Monitor", "endpoint": "Monitor"}
        assert assets.get_route({"Category": "Laptop"}) == {"itemtype": "Computer", "endpoint": "Computer"}
