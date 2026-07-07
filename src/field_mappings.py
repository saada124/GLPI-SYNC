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
        self.constants: dict[str, Any] = config.get("constants", {})
        self.helper_columns: dict[str, str] = config.get("helper_columns", {})
        self.inline_values: dict[str, Any] = {}

    @property
    def glpi_id_col(self) -> str | None:
        return self.helper_columns.get("glpi_id")

    @property
    def synced_at_col(self) -> str | None:
        return self.helper_columns.get("synced_at")

    @property
    def modified_at_col(self) -> str | None:
        return self.helper_columns.get("modified_at")

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
        payload.update(self.inline_values)
        return {k: v for k, v in payload.items() if v is not None and v != ""}

    def lookup_code(self, field: str, value: str) -> Any:
        table = self.code_lookups.get(field, {})
        return table.get(str(value).strip(), value)


def load_mappings() -> dict[str, EntityMapping]:
    with open(MAPPINGS_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    entities = raw.get("entities", {})
    return {name: EntityMapping(name, cfg) for name, cfg in entities.items()}
