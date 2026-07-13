from pathlib import Path
from typing import Any
import yaml


MAPPINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "mappings.yaml"


class EntityMapping:
    def __init__(self, name: str, config: dict):
        self.name = name
        self.sheet_tab: str = config["sheet_tab"]
        self.glpi_itemtype: str = config["glpi_itemtype"]
        self.api_endpoint: str = config["api_endpoint"]
        self.id_field: str = config.get("id_field", "id")
        self.fields: dict[str, str] = config.get("fields", {})
        self.code_lookups: dict[str, dict[str, int]] = config.get("code_lookups", {})
        self.lookups: dict[str, str] = config.get("lookups", {})
        self.constants: dict[str, Any] = config.get("constants", {})
        self.helper_columns: dict[str, str] = config.get("helper_columns", {})
        routing = config.get("itemtype_routing")
        if routing:
            self.routing_field: str | None = routing.get("category_field")
            self.routing_fallback: str | None = routing.get("fallback_field")
            self.routing_default: str | None = routing.get("default_itemtype")
            self.routing_default_endpoint: str | None = routing.get("default_endpoint")
            self.routing_map: dict[str, dict[str, str]] = routing.get("mapping", {})
        else:
            self.routing_field = None
            self.routing_fallback = None
            self.routing_default = None
            self.routing_default_endpoint = None
            self.routing_map = {}

    @property
    def glpi_id_col(self) -> str | None:
        return self.helper_columns.get("glpi_id")

    @property
    def synced_at_col(self) -> str | None:
        return self.helper_columns.get("synced_at")

    @property
    def modified_at_col(self) -> str | None:
        return self.helper_columns.get("modified_at")

    def get_route(self, row: dict[str, Any]) -> dict[str, str] | None:
        if not self.routing_field:
            return None
        val = str(row.get(self.routing_field, "")).strip()
        if val == "Autre" and self.routing_fallback:
            val = str(row.get(self.routing_fallback, "")).strip()
        route = self.routing_map.get(val)
        if route:
            return route
        if self.routing_default:
            return {"itemtype": self.routing_default, "endpoint": self.routing_default_endpoint or self.routing_default}
        return None

    def sheet_to_glpi(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = {}
        for sheet_col, glpi_field in self.fields.items():
            if not glpi_field:
                continue
            raw = row.get(sheet_col, "")
            if sheet_col in self.code_lookups:
                raw_str = str(raw).strip()
                payload[glpi_field] = self.code_lookups[sheet_col].get(raw_str, raw)
            else:
                payload[glpi_field] = raw
        payload.update(self.constants)
        return {k: v for k, v in payload.items() if v is not None and v != ""}


def load_mappings() -> dict[str, EntityMapping]:
    with open(MAPPINGS_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    entities = raw.get("entities", {})
    return {name: EntityMapping(name, cfg) for name, cfg in entities.items()}
