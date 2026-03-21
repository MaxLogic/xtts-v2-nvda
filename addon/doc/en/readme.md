# MaxLogic XTTS v2 for NVDA

MaxLogic XTTS v2 is an NVDA add-on that adds a separate XTTS v2 speech synthesizer under the name `MaxLogic XTTS v2`.

It is based on the `kokoro-tts-nvda` project structure, but the speech runtime is swapped to Coqui XTTS v2 and voice management is centered around XTTS reference-audio profiles instead of Kokoro embeddings.

## Features

- Separate NVDA synth: `MaxLogic XTTS v2`
- One-click runtime setup from the voice manager
- Built-in voice manager available from the NVDA menu
- Installed, Official, Community, and Speech Cache tabs
- Five bundled CC0 starter voices
- Curated on-demand voice downloads from legal upstream sources
- Local profile install from `.wav`, `.mp3`, `.flac`, `.ogg`, `.m4a`, `.aac`, or `.zip`
- Sample playback before installing a catalog profile
- User-managed voice profiles stored outside the add-on so they survive reinstalls
- Persistent short-speech cache and short-lived paragraph hot cache
- Safer text chunking for long passages

## Runtime model

The add-on is helper-first. The recommended setup is:

1. Install the add-on normally. The installer bootstraps the helper runtime automatically.
2. Open `NVDA menu -> MaxLogic XTTS v2 voice manager...` and use `Set up XTTS runtime` if the helper runtime is missing or needs repair.
3. The setup step installs the latest released `coqui-tts` package from PyPI.
4. Select `MaxLogic XTTS v2` as the synthesizer in NVDA.

The bootstrap script installs the latest released `coqui-tts` package from PyPI, together with the runtime dependencies it needs.

By default the helper uses the Coqui model name `tts_models/multilingual/multi-dataset/xtts_v2`.

## Voice profiles

XTTS v2 clones from reference audio. In this add-on, a "voice" is a profile that contains one or more reference audio files plus profile metadata.

The add-on bundles these small CC0 starter voices:

- `Gosia`
- `Darkman`
- `Kathleen`
- `Joe`
- `Kerstin`

User-installed profiles are stored here:

- `%APPDATA%\nvda\maxlogicXTTSv2\voices`

Speech cache and logs are also stored outside the add-on package:

- Cache settings: `%APPDATA%\nvda\maxlogicXTTSv2\speech-cache-settings.json`
- Persistent cache database: `%APPDATA%\nvda\maxlogicXTTSv2\cache\speech-cache.sqlite3`
- Helper log: `%APPDATA%\nvda\maxlogicXTTSv2\logs\helper.log`

## Voice manager

Open:

- `NVDA menu -> MaxLogic XTTS v2 voice manager...`

Tabs:

- `Installed`: user-installed and packaged voice profiles
- `Official`: curated downloadable voice profiles from legal upstream sources
- `Community`: curated community profile catalog entries from Thorsten-Voice and Kyutai
- `Speech Cache`: cache settings, stats, clear, and compact actions

## Supported languages

The bundled XTTS v2 integration normalizes NVDA language tags to the language set documented by Coqui XTTS v2:

- `ar`, `cs`, `de`, `en`, `es`, `fr`, `hu`, `it`, `ja`, `ko`, `nl`, `pl`, `pt`, `ru`, `tr`, `zh-cn`

## Notes

- This repository does not bundle the large XTTS model payload by default.
- The helper environment is meant to be rebuilt from the latest released Coqui packages, not pinned to an older release.
- The Official tab now ships with curated CC0 downloadable voices from OHF Voice.
- The Community tab includes curated entries from Thorsten-Voice and Kyutai.
- The add-on can be managed from NVDA even when the synth itself is unavailable because the helper environment or voice profiles have not been installed yet.

## Add-on identity

- Add-on ID: `maxlogicXTTSv2`
- Synth driver: `maxlogic_xtts_v2`
- Display name: `MaxLogic XTTS v2`
