# Tasks

## Next - Later

### T-001 [CATALOG] Curate official XTTS profile bundles

Populate `addon/synthDrivers/maxlogic_xtts_v2/catalog.json` with curated official XTTS profile bundles.

### T-002 [CATALOG] Curate community XTTS profile bundles

Populate `addon/synthDrivers/maxlogic_xtts_v2/community_catalog.json` with curated community profile bundles.

### T-003 [SPEECH] Tune say-all utterance lead timing

Tune `utteranceLeadSeconds` (1.0 s) from say-all in NVDA. Log how long the first chunk of each utterance takes and how early its end index was reported.

### T-004 [CACHE] Batch speech-cache hit updates

`SpeechCache.get_audio` commits a write on every hit to update the use count. Batch those updates if the cache grows.

### T-005 [RUNTIME] Add cancellation and timeouts to runtime setup

Runtime setup (`service.run_runtime_setup`) has no timeout and cannot be cancelled. Neither can extracting a sample.

### T-006 [UI] Build page layouts with NVDA GUI helpers

Page layouts are built by hand. Move them to `gui.guiHelper.BoxSizerHelper` and `ButtonHelper` for NVDA spacing.

### T-007 [UI] Share catalog and Hugging Face panel behavior

`HuggingFaceSearchPanel` repeats about 80% of `CatalogVoicesPanel`. Move their filters, checklist, download and preview behavior into one shared base class in `catalog_page.py`.

### T-008 [REFACTOR] Split the voice manager by page

Split `voice_manager.py` (3,231 lines):

- One module per page: `installed_page.py` (`InstalledVoicesPanel`, lines 149 to 559), `catalog_page.py` (`CatalogVoicesPanel`), `huggingface_page.py`, `browse_page.py`, `extract_page.py` (`ExtractSamplePanel`, about 1,100 lines, the largest), `cache_page.py` and `dialog.py` (`MaxLogicVoiceManagerDialog`).
- Keep `voice_manager.py` as a two-line module that re-exports `MaxLogicVoiceManagerDialog`, so `__init__.py` and existing imports keep working.
- Order: the cache page first (it shares nothing), then installed, extract, and the catalog pages last, together with the shared base. Move code without changing it, one page per commit, and run the tests after each.

## Blocked

### T-009 [TEST] Validate XTTS behavior in a real NVDA installation

This task needs a real NVDA installation with Coqui TTS available. Validate:

- helper bootstrap and runtime;
- say-all through a long document, with no pauses between lines or sentences, cursor follow, and immediate Control cancellation;
- cloning a voice while the manager is still loading XTTS;
- cycling voices in the settings ring while speaking.
