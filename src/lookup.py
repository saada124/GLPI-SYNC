from __future__ import annotations

from typing import Any

from glpi_api import GLPIAPI
from logger import setup_logger

logger = setup_logger()


class LookupMap:
    def __init__(self):
        self._name_to_id: dict[str, int] = {}
        self._id_to_name: dict[int, str] = {}

    def add(self, name: str, id: int) -> None:
        self._name_to_id[name] = id
        self._id_to_name[id] = name

    def resolve(self, value: str) -> int | None:
        return self._name_to_id.get(str(value).strip())

    def resolve_reverse(self, id: int) -> str | None:
        return self._id_to_name.get(int(id))


class LookupCache:
    def __init__(self, glpi: GLPIAPI, mappings: dict[str, Any]):
        self._maps: dict[str, LookupMap] = {}
        self._load_all(glpi, mappings)

    def get_map(self, ref_type: str) -> LookupMap | None:
        return self._maps.get(ref_type)

    def resolve(self, ref_type: str, value: str) -> int | None:
        m = self.get_map(ref_type)
        return m.resolve(value) if m else None

    def resolve_reverse(self, ref_type: str, id: int) -> str | None:
        m = self.get_map(ref_type)
        return m.resolve_reverse(id) if m else None

    def _load_all(self, glpi: GLPIAPI, mappings: dict[str, Any]) -> None:
        all_ref_types: set[str] = set()
        for mapping in mappings.values():
            for ref_type in (mapping.lookups or {}).values():
                all_ref_types.add(ref_type)

        for ref_type in sorted(all_ref_types):
            try:
                self._fetch_type(glpi, ref_type)
            except Exception as e:
                logger.warning(f"Failed to load lookup '{ref_type}': {e}")

    def _fetch_type(self, glpi: GLPIAPI, ref_type: str) -> None:
        lmap = LookupMap()

        if ref_type == "Supplier":
            items = glpi.get_suppliers() or []
            for item in items:
                if item.get("name") and item.get("id"):
                    lmap.add(item["name"], int(item["id"]))

        elif ref_type == "ITILCategory":
            items = glpi.get_itil_categories() or []
            for item in items:
                if item.get("name") and item.get("id"):
                    lmap.add(item["name"], int(item["id"]))

        elif ref_type == "ComputerType":
            items = glpi.get_computer_types() or []
            for item in items:
                if item.get("name") and item.get("id"):
                    lmap.add(item["name"], int(item["id"]))

        elif ref_type == "User":
            try:
                items = glpi.search("User") or []
                for item in items:
                    uid = item.get("id")
                    name = item.get("name")
                    if name and uid:
                        lmap.add(name, int(uid))
            except Exception:
                logger.warning("Could not load Users for lookups")

        else:
            logger.warning(f"Unknown lookup type: {ref_type}")
            return

        self._maps[ref_type] = lmap
        logger.info(f"Loaded {len(lmap._name_to_id)} entries for '{ref_type}' lookups")
