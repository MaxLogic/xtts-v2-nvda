# Tasks

- Populate `addon/synthDrivers/maxlogic_xtts_v2/catalog.json` with curated official XTTS profile bundles.
- Populate `addon/synthDrivers/maxlogic_xtts_v2/community_catalog.json` with curated community profile bundles.
- Validate helper bootstrap and runtime on a real NVDA installation with Coqui TTS available.

## From the September 2026 review

Speech:
- Play streamed audio as it arrives. `MAXLOGIC_XTTS_V2_LIVE_STREAM_PLAYBACK` has no effect on first audio: `_synthesize_chunk_audio_items` collects every streamed piece before any is played. The first streamed piece arrives after 647 ms at the median; a whole short chunk after 1,240 ms.
- Tune `utteranceLeadSeconds` (1.0 s) from say-all in NVDA. Log how long the first chunk of each utterance takes and how early its end index was reported.
- `SpeechCache.get_audio` commits a write on every hit to update the use count. Batch those updates if the cache grows.

Voice manager:
- Runtime setup (`service.run_runtime_setup`) has no timeout and cannot be cancelled. Neither can extracting a sample.
- Page layouts are built by hand. Move them to `gui.guiHelper.BoxSizerHelper` and `ButtonHelper` for NVDA spacing.
- `HuggingFaceSearchPanel` repeats about 80% of `CatalogVoicesPanel`.
- Split `voice_manager.py` (3,231 lines). Proposal:
  - One module per page: `installed_page.py` (`InstalledVoicesPanel`, lines 149 to 559), `catalog_page.py` (`CatalogVoicesPanel`), `huggingface_page.py`, `browse_page.py`, `extract_page.py` (`ExtractSamplePanel`, about 1,100 lines, the largest), `cache_page.py` and `dialog.py` (`MaxLogicVoiceManagerDialog`).
  - Move the shared parts of the catalog and Hugging Face pages (filters, checklist, download, preview) into one base class in `catalog_page.py`.
  - Keep `voice_manager.py` as a two-line module that re-exports `MaxLogicVoiceManagerDialog`, so `__init__.py` and existing imports keep working.
  - Order: the cache page first (it shares nothing), then installed, extract, and the catalog pages last, together with the shared base. Move code without changing it, one page per commit, and run the tests after each.

Manual checks in NVDA, not yet done:
- Say-all through a long document: no pauses between lines or sentences, the cursor follows the speech, and Control stops it at once.
- Clone a voice while the manager is still loading XTTS.
- Cycle voices in the settings ring while speaking.
