import logging
import unicodedata
from pathlib import Path
from typing import Any
import yaml


logger = logging.getLogger("glpi-sync")

MAPPINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "mappings.yaml"


def _normalize_category(val: str) -> str:
    """Normalize a category string for matching: strip, casefold, strip accents.

    Makes 'Écran', ' écran', 'ECRAN', 'Ecran' all match the same routing key,
    so drift between an AppSheet dropdown label and mappings.yaml (whitespace,
    casing, accent encoding) doesn't fall through to the default itemtype.
    """
    val = val.strip()
    val = unicodedata.normalize("NFKD", val)
    val = "".join(c for c in val if not unicodedata.combining(c))
    return val.casefold()


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
            # Pre-normalized lookup so matching is resilient to whitespace/
            # casing/accent drift between the AppSheet dropdown and this YAML.
            self._routing_map_normalized: dict[str, dict[str, str]] = {
                _normalize_category(k): v for k, v in self.routing_map.items()
            }
        else:
            self.routing_field = None
            self.routing_fallback = None
            self.routing_default = None
            self.routing_default_endpoint = None
            self.routing_map = {}
            self._routing_map_normalized = {}

    @property
    def glpi_id_col(self) -> str | None:
        return self.helper_columns.get("glpi_id")

    @property
    def synced_at_col(self) -> str | None:
        return self.helper_columns.get("synced_at")

    @property
    def modified_at_col(self) -> str | None:
        return self.helper_columns.get("modified_at")

    @property
    def created_at_col(self) -> str | None:
        return self.helper_columns.get("created_at")

    @property
    def glpi_itemtype_col(self) -> str | None:
        configured = self.helper_columns.get("glpi_itemtype")
        if configured:
            return configured
        if self.routing_field:
            return "GLPI_Itemtype"
        return None

    def get_route(self, row: dict[str, Any]) -> dict[str, str] | None:
        if not self.routing_field:
            return None

        val = str(row.get(self.routing_field, "")).strip()
        if val == "Autre" and self.routing_fallback:
            val = str(row.get(self.routing_fallback, "")).strip()

        if not val:
            # Genuinely blank category — this is what the default is for.
            if self.routing_default:
                return {
                    "itemtype": self.routing_default,
                    "endpoint": self.routing_default_endpoint or self.routing_default,
                }
            return None

        # Try exact match first (fast path, no surprises for well-formed data).
        route = self.routing_map.get(val)
        if route:
            return route

        # Fall back to normalized match — catches whitespace/casing/accent
        # drift between the AppSheet dropdown and this YAML's mapping keys.
        route = self._routing_map_normalized.get(_normalize_category(val))
        if route:
            return route

        # Category was set but doesn't match anything we know about.
        # Do NOT silently fall back to the default itemtype here — that's
        # exactly how mismatched/renamed categories used to end up as
        # Computer. Warn loudly and skip instead so it gets noticed.
        logger.warning(
            f"[{self.name}] Unrecognized category '{val}' — no routing match "
            f"in mappings.yaml (checked exact and normalized). Row skipped. "
            f"Add '{val}' to itemtype_routing.mapping for '{self.name}' if this "
            f"is a valid AppSheet dropdown choice."
        )
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