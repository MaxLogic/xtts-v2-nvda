# MaxLogic XTTS v2 for NVDA

MaxLogic XTTS v2 is an NVDA add-on that adds a separate XTTS v2 speech synthesizer under the name `MaxLogic XTTS v2`.

It is based on the `kokoro-tts-nvda` project structure, but the speech runtime is swapped to Coqui XTTS v2 and voice management is centered around XTTS reference-audio profiles instead of Kokoro embeddings.

## Features

- Separate NVDA synth: `MaxLogic XTTS v2`
- One-click runtime setup from the voice manager
- Built-in voice manager available from the NVDA menu
- Installed, Clone Voice, Browse Voices, Extract Sample, and Speech Cache tabs
- Five bundled CC0 starter voices
- Curated on-demand voice downloads from legal upstream sources
- Unified voice browser for Hugging Face search, official profiles, and community profiles
- Local profile install from `.wav`, `.mp3`, `.flac`, `.ogg`, `.m4a`, `.aac`, `.pth`, or `.zip`
- Direct `.pth` install validates that the file is a compatible XTTS voice-conditioning file before accepting it
- Sample playback before installing a catalog profile
- Extract a short XTTS-ready sample from a longer recording with start/end markers
- Edit extraction sources safely by deleting marked snippets from a temporary working copy
- Save the current marked snippet or preserve the edited working copy as a separate audio file
- Keyboard shortcuts for Extract Sample transport, markers, preview, deletion, and saving
- User-managed voice profiles stored outside the add-on so they survive reinstalls
- Persistent short-speech cache and short-lived paragraph hot cache
- Streamed uncached XTTS speech when the helper runtime supports it
- Safer text chunking for long passages

## Runtime model

The add-on is helper-first. The recommended setup is:

1. Install the add-on normally. The installer bootstraps the helper runtime automatically.
2. Open `NVDA menu -> MaxLogic XTTS v2 voice manager...` and use `Set up XTTS runtime` if the helper runtime is missing or needs repair.
3. The setup step installs the pinned helper runtime tested with this add-on.
4. Select `MaxLogic XTTS v2` as the synthesizer in NVDA.

The bootstrap script installs Python packages pinned for the tested XTTS streaming stack: `coqui-tts==0.27.5`, `transformers==4.57.6`, `torchcodec==0.16.0`, `numpy<2`, and PyTorch/Torchaudio 2.11.0 for the selected provider. Auto setup prefers Python 3.11 when available because that is the tested environment.

By default the helper uses the Coqui model name `tts_models/multilingual/multi-dataset/xtts_v2`.

Normal NVDA speech cancellation stops current audio without restarting the XTTS helper. This keeps the warmed CUDA runtime available for the next utterance; the helper is closed when the synth is terminated or NVDA switches away from it.

XTTS is still a large neural voice-cloning model, so uncached text is much slower than classic screen-reader synths. With the pinned helper runtime, the driver streams generated audio chunks as they become available, then relies on the persistent speech cache for repeated UI text. If a different helper runtime does not report streaming support, the add-on falls back to full-buffer synthesis.

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
- `Clone Voice`: combine reference recordings and save voice conditioning with adjustable settings
- `Browse Voices`: Hugging Face search, official downloadable profiles, and curated community profiles
- `Extract Sample`: load a long recording, set markers, preview the selection, and save it as an XTTS profile
- `Speech Cache`: cache settings, stats, clear, and compact actions

Browse Voices:

- Use the Source selector to switch between Hugging Face search, the official catalog, and the community catalog
- Searches live Hugging Face model repos for XTTS-related entries that include playable sample audio
- Uses the sample audio as the install source, then lets XTTS build its own conditioning cache locally
- Keeps repo and license metadata in the installed profile metadata when available
- Filters out results that do not expose a usable sample file for preview/install
- The official source ships curated CC0 downloadable voices from OHF Voice
- The community source includes curated entries from Thorsten-Voice and Kyutai

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
- The helper environment is pinned to the tested Coqui/PyTorch stack because newer Coqui releases were slower to first audio in local testing and the current `inference_stream` path hung.
- The Browse Voices tab consolidates Hugging Face search, the official catalog, and the community catalog under one source selector.
- The add-on can be managed from NVDA even when the synth itself is unavailable because the helper environment or voice profiles have not been installed yet.

## Keyboard navigation and feedback

Use `Ctrl+Tab` and `Ctrl+Shift+Tab` to change tabs, then `Tab` and `Shift+Tab` to move between controls. Use arrow keys in lists and `Space` to check a voice for download. Button access keys are shown by Windows when you press `Alt`. `Escape` closes the manager when no operation is running.

Optional pages load when selected. Downloads, cache operations, and voice installation run in the background with progress feedback. Status and voice details are read-only text fields: focus them to review or copy their contents. The sample editor and cache page scroll to keep focused controls in view. Closing waits while an audio edit or save is running.

### Installed voice previews

Select a voice in either installed-voice list and press `Ctrl+P` or `Alt+P` to play its sample. Press the shortcut again during playback to stop it. When playback finishes, focus returns to the same list, so you can select the next voice with an arrow key and press `Ctrl+P` again. Focus is not moved if you switched to another application or tab.

Use **Open voice folder** (`Alt+O`) to open the selected profile's containing folder in File Explorer. This works for user-installed and packaged profiles.

The **Page status** field reports loading for the selected tab. NVDA announces loading and completion while the manager is active.

Installed-voice previews are cached as complete WAV files under `%APPDATA%\nvda\maxlogicXTTSv2\cache\preview-wav`. A replay with the same voice files, sample text, and language uses that file without starting the speech model. Changing the voice files or preview language produces a new cache entry. The first uncached preview still needs to load the XTTS model; after startup it streams audio as it becomes available. If playback is stopped, generation may finish in the background to complete the cached sample.

## Add-on identity

- Add-on ID: `maxlogicXTTSv2`
- Synth driver: `maxlogic_xtts_v2`
- Display name: `MaxLogic XTTS v2`

## NVDA compatibility and checks

This release targets NVDA 2026.2. The minimum supported version is 2024.1.

Run the repository checks with `python -m unittest discover -s tests -v`. Runtime synthesis and physical keyboard/speech checks are separate from these tests.

### Clone Voice

Open **Clone Voice**, add one or more clear recordings of the same speaker, then enter a unique voice name and choose the default preview language. Use **Extract Sample** first when a recording needs trimming. **Create voice** computes and saves the voice conditioning and copies the recordings into the user voice profile. Existing voices are preserved; choose another name if it is already used. After creation, **Play created voice sample** previews the saved profile.

Reference volume normalization is optional and off by default. **Show advanced settings** reveals:

- **Maximum seconds per recording** (default 30): how much of each recording XTTS reads.
- **Total conditioning seconds** (default 6): how much of the joined recordings is used for GPT conditioning.
- **Conditioning chunk seconds** (default 6): chunk size within that conditioning audio; it must not exceed the total.

Longer values do not guarantee better results. These settings affect voice conditioning; they are not speaking-speed or synthesis-temperature controls. Model startup can take tens of seconds. Creation runs in a worker while NVDA remains responsive, and the status dialog closes when it finishes. The created profile appears in Installed, where its folder can be opened.


### Choosing recordings and presets

The Clone Voice tab has **Help: choosing recordings** (Alt+H) and **Check recordings** (Alt+K). The checker reports duration, channels and sample rate without loading XTTS. It suggests PCM WAV or FLAC conversion only if decoding fails; stereo or 48 kHz alone does not require conversion. XTTS handles mono mixing and resampling internally. Its output is 24 kHz.

Use clear recordings of one speaker with consistent sound and little edge silence. Roughly 6–15 seconds per clip is a starting point, not a quality guarantee. Several clean references may help, but more files are not automatically better than one good recording. Speaker embeddings are averaged; GPT conditioning uses the selected duration from joined recordings in list order. Optional edge trimming preserves original files, retains about 100 ms of margin and keeps internal pauses. It is off by default.

Reference conditioning presets use maximum-reference / total-conditioning / chunk seconds: Default **30 / 6 / 6**, Extended **30 / 12 / 6**, Longer **30 / 30 / 6**. Advanced controls remain editable.

Speech generation presets are separate and saved with each newly created voice:

| Preset | Temperature | Top p | Top k | Repetition penalty | Speed |
| --- | --- | --- | --- | --- | --- |
| XTTS inference defaults | 0.75 | 0.85 | 50 | 10 | 1.0 |
| Suggested range: midpoint | 0.75 | 0.85 | 50 | 2 | 1.0 |
| Suggested range: lower | 0.65 | 0.80 | 50 | 2 | 1.0 |
| Suggested range: upper | 0.85 | 0.90 | 50 | 2 | 1.0 |

These are starting points for listening comparisons, not ranked quality presets. Speed multiplies NVDA's rate. Settings affect both streamed and buffered synthesis. Changing controls does not edit an existing voice. Saved conditioning avoids recloning, but generating new text still needs the model; a matching preview WAV does not. Cache identities include profile files and speech settings.

References checked: [Coqui package](https://pypi.org/project/coqui-tts/), [XTTS 0.27.5 source](https://github.com/idiap/coqui-ai-TTS/blob/v0.27.5/TTS/tts/models/xtts.py), [voice caching](https://coqui-tts.readthedocs.io/en/latest/cloning.html).
