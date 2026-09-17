"""The bundled catalog must stay visible after a voice install or add-on update."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SYNTH_ROOT = Path(__file__).resolve().parents[1] / "addon/synthDrivers/maxlogic_xtts_v2"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self._data = tempfile.TemporaryDirectory(prefix="xtts-catalog-test-")
        self.addCleanup(self._data.cleanup)
        patcher = patch.dict(os.environ, {"APPDATA": self._data.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.path.insert(0, str(SYNTH_ROOT))
        self.addCleanup(sys.path.remove, str(SYNTH_ROOT))
        for name in ("_catalog", "_voice_store", "_paths"):
            sys.modules.pop(name, None)
        import _catalog
        self.catalog = _catalog

    def test_cache_written_by_an_older_version_does_not_hide_bundled_entries(self):
        stale = {"schemaVersion": self.catalog.CATALOG_SCHEMA_VERSION, "entries": [{"id": "only-in-old-cache"}]}
        with open(self.catalog._catalog_cache_path("official"), "w", encoding="utf-8") as handle:
            json.dump(stale, handle)
        bundled_ids = [entry["id"] for entry in self.catalog.load_bundled_catalog("official")["entries"]]
        entries, payload = self.catalog.get_catalog_entries("official")
        self.assertEqual([entry["id"] for entry in entries], bundled_ids)
        self.assertEqual(payload["source"], "bundled")


if __name__ == "__main__":
    unittest.main()
