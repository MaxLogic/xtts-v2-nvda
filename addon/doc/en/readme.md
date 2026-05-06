# MaxLogic XTTS v2 for NVDA

MaxLogic XTTS v2 is an NVDA add-on that adds a separate XTTS v2 speech synthesizer under the name `MaxLogic XTTS v2`.

It is based on the `kokoro-tts-nvda` project structure, but the speech runtime is swapped to Coqui XTTS v2 and voice management is centered around XTTS reference-audio profiles instead of Kokoro embeddings.

## Features

- Separate NVDA synth: `MaxLogic XTTS v2`
- One-click runtime setup from the voice manager
- Built-in voice manager available from the NVDA menu
- Installed, Hugging Face, Official, Community, Extract Sample, and Speech Cache tabs
- Five bundled CC0 starter voices
- Curated on-demand voice downloads from legal upstream sources
- Live Hugging Face search for XTTS-compatible repos with playable sample audio
- Local profile install from `.wav`, `.mp3`, `.flac`, `.ogg`, `.m4a`, `.aac`, `.pth`, or `.zip`
- Direct `.pth` install validates that the file is a compatible XTTS voice-conditioning file before accepting it
- Sample playback before installing a catalog profile
- Extract a short XTTS-ready sample from a longer recording with start/end markers
- Edit extraction sources safely by deleting marked snippets from a temporary working copy
- Save the current marked snippet or preserve the edited working copy as a separate audio file
- Keyboard shortcuts for Extract Sample transport, markers, preview, deletion, and saving
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

Normal NVDA speech cancellation stops current audio without restarting the XTTS helper. This keeps the warmed CUDA runtime available for the next utterance; the helper is closed when the synth is terminated or NVDA switches away from it.

XTTS is still a large neural voice-cloning model, so uncached text is much slower than classic screen-reader synths. The driver favors a short first chunk for lower initial latency, then relies on the persistent speech cache for repeated UI text.

## Voice profiles

XTTS v2 clones from reference audio. In this add-on, a "voice" is a profile that contains one or more reference audio files, or an XTTS conditioning `.pth`, plus profile metadata.

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
- `Hugging Face`: live search for XTTS-compatible repos with sample preview and one-click install from sample audio
- `Official`: curated downloadable voice profiles from legal upstream sources
- `Community`: curated community profile catalog entries from Thorsten-Voice and Kyutai
- `Extract Sample`: load a long recording, set markers, preview the selection, and save it as an XTTS profile
- `Speech Cache`: cache settings, stats, clear, and compact actions

Hugging Face search:

- Searches live Hugging Face model repos for XTTS-related entries that include playable sample audio
- Uses the sample audio as the install source, then lets XTTS build its own conditioning cache locally
- Keeps repo and license metadata in the installed profile metadata when available
- Filters out results that do not expose a usable sample file for preview/install

Direct local install:

- Audio files such as `.wav` and `.mp3` install as reference-audio profiles
- `.pth` files install only if the helper can load them as a valid XTTS voice-conditioning file
- Invalid, corrupted, or unrelated PyTorch `.pth` files are rejected before installation

Extract Sample editing:

- Source recordings, including `.m4a` files when ffmpeg is available, are decoded to a temporary WAV working file before editing, so the original recording is not changed
- Use Start marker and End marker to select interruptions, then use `Delete snippet` to remove that range from the working copy
- Use the 3-second marker preview buttons to listen before or after the start and end markers; `Preview selection` starts the marked range and stops it when pressed again
- The working copy is used for playback, preview, and saving the final XTTS profile
- Edit Current position directly to seek; the typed value is applied when the field loses focus or when Enter is pressed
- Temporary working files are deleted when another source recording is selected or the voice manager closes
- Large uncompressed `.wav` sources stream directly for responsive playback and seeking when the system media control cannot load them
- The panel shows whether the current selection is shorter than, longer than, or within the recommended 10 to 30 second XTTS sample range
- Use `Save current snippet as audio` to export the selected range as a `.wav`, or `Save edited audio as...` after deleting snippets to preserve the modified WAV working copy
- Keyboard shortcuts: `Ctrl+O` browse, `Ctrl+P` play or pause, `Ctrl+K` stop, `Ctrl+Left`/`Ctrl+Right` move 5 seconds, `Ctrl+Shift+Left`/`Ctrl+Shift+Right` move 30 seconds, `Ctrl+1` set start, `Ctrl+2` set end, `Ctrl+R` preview selection, `Ctrl+D` delete snippet, `Ctrl+E` save current snippet, `Ctrl+M` save edited audio, and `Ctrl+S` save profile

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
