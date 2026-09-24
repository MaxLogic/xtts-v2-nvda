# MaxLogic XTTS v2 for NVDA

MaxLogic XTTS v2 is an NVDA add-on that adds a separate XTTS v2 speech synthesizer under the name `MaxLogic XTTS v2`.

It is based on the `kokoro-tts-nvda` project structure, but the speech runtime is swapped to Coqui XTTS v2 and voice management is centered around XTTS reference-audio profiles instead of Kokoro embeddings.

This is a high-performance XTTS v2 implementation. On an NVIDIA GPU it generates speech about three times faster than stock Coqui XTTS, and new text starts to speak after about a quarter of a second. It picks exactly the same audio tokens as Coqui, so voices sound the same. See [Performance](#performance) for how it gets there.

## Features

- Separate NVDA synth: `MaxLogic XTTS v2`
- One-click runtime setup from the voice manager
- Built-in voice manager available from the NVDA menu
- Installed, Clone Voice, Browse Voices, Extract Sample, Speech Cache, and Loading Sounds tabs
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
- About three times faster than stock Coqui XTTS on an NVIDIA GPU: each decoding step replays as one CUDA graph
- Speech streams as it is generated: new text starts after about 0.26 s and plays without gaps
- Text is sent in whole sentences, within XTTS's own length limit for each language
- Say-all without pauses between lines; the next sentence is synthesized while the current one plays
- Selecting the synth never freezes NVDA: the model loads in the background
- Spoken announcements when XTTS starts loading, every 10 seconds while it is still loading, and when it is ready, each one optional and replaceable
- Persistent short-speech cache, short-lived paragraph hot cache, and cached voice conditioning

## Performance

Measured with a cloned voice on an RTX 3090 under Windows 11 (September 2026):

| | Stock Coqui XTTS | This add-on |
| --- | --- | --- |
| Time per audio token | 36 ms | 12 ms |
| Generation time per second of speech | 0.8 to 0.9 s | 0.28 s |
| First audio of new text | about 0.7 s | about 0.26 s |
| Gaps while a long sentence plays | some | none |

How it gets there:

- **CUDA graph decoding.** Stock XTTS decodes through Hugging Face `generate()`, which sends about 700 small jobs to the GPU for every audio token. On Windows that overhead, not the GPU, sets the speed: the GPU stays about 9% busy. The helper records one decoding step once as a CUDA graph and replays it for every token. Sampling follows Coqui's rules in the same order, and tests confirm it picks the same tokens as Coqui. If the graph ever fails, the helper switches back to Coqui's decoding by itself.
- **One helper process that stays loaded.** The model loads once, in a separate helper process, and serves every utterance. Loading takes 35 to 60 s and happens in the background, so NVDA stays responsive.
- **Streaming.** Audio plays as each piece, about 0.9 s of speech, is generated. The end of each line is reported to NVDA one second before its audio ends, so say-all prepares the next line while the current one finishes.
- **Whole sentences.** Text goes to XTTS in whole sentences, packed up to 80% of its limit for the language (200 characters for English). Fewer requests mean fewer waits and a natural sentence melody.
- **Caches.** Short, repeated phrases come from a persistent cache without running the model. Recently spoken paragraphs come from a short-lived memory cache. Voice conditioning is computed once per voice and stored.
- **Early cancellation.** When you interrupt speech, the helper stops generating the old text instead of finishing it.

Without an NVIDIA GPU, XTTS runs on the CPU, without CUDA graphs, and is much slower. Cached phrases stay instant.

Environment variables for troubleshooting, read when NVDA starts:

- `MAXLOGIC_XTTS_V2_CUDA_GRAPH=0` decodes the stock way instead of with a CUDA graph.
- `MAXLOGIC_XTTS_V2_LIVE_STREAM_PLAYBACK=0` waits for each whole chunk before playing it.
- `MAXLOGIC_XTTS_V2_GPU=cpu` runs XTTS on the CPU.

## Runtime model

The add-on is helper-first. The recommended setup is:

1. Install the add-on normally. The installer bootstraps the helper runtime automatically.
2. Open `NVDA menu -> MaxLogic XTTS v2 voice manager...` and use `Set up XTTS runtime` if the helper runtime is missing or needs repair.
3. The setup step installs the pinned helper runtime tested with this add-on.
4. Select `MaxLogic XTTS v2` as the synthesizer in NVDA.

The bootstrap script installs Python packages pinned for the tested XTTS streaming stack: `coqui-tts==0.27.5`, `transformers==4.57.6`, `torchcodec==0.16.0`, `numpy<2`, and PyTorch/Torchaudio 2.11.0 for the selected provider. Auto setup prefers Python 3.11 when available because that is the tested environment.

By default the helper uses the Coqui model name `tts_models/multilingual/multi-dataset/xtts_v2`.

Normal NVDA speech cancellation stops current audio without restarting the XTTS helper. This keeps the warmed CUDA runtime available for the next utterance; the helper is closed when the synth is terminated or NVDA switches away from it.

Selecting `MaxLogic XTTS v2`, or starting NVDA with it, does not wait for the model. NVDA lists the installed voices at once and plays a short "Loading X T T S" announcement. Every 10 seconds while it is still loading, "Still loading, please wait" plays. Speech sent while the model loads waits, and "X T T S is ready" plays before it starts. Turn these sounds off or replace them on the Loading Sounds page of the voice manager.

XTTS is still a large neural voice-cloning model, so uncached text needs the model, unlike classic screen-reader synths. With the pinned helper runtime on an NVIDIA GPU, it generates speech about three times faster than it plays, streams it as it is generated, and serves repeated UI text from the persistent speech cache. If a different helper runtime does not report streaming support, the add-on falls back to full-buffer synthesis.

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
- Loading sound settings: `%APPDATA%\nvda\maxlogicXTTSv2\loading-sounds.json`
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
- `Loading Sounds`: turn the sounds for XTTS starting to load, still loading and being ready on or off, choose other WAV files for them, and set how often the still loading sound repeats (3 to 120 seconds, 10 by default)

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

Use `Ctrl+Tab` and `Ctrl+Shift+Tab` to change tabs, then `Tab` and `Shift+Tab` to move between controls. Use arrow keys in lists and `Space` to check a voice for download. Button access keys are shown by Windows when you press `Alt`. `Escape`, `Ctrl+F4`, Close and the window X close the manager when no operation is running.

Optional pages load when selected. Downloads, cache operations, and voice installation run in the background with progress feedback. Status and voice details are read-only text fields: focus them to review or copy their contents. The sample editor and cache page scroll to keep focused controls in view. Closing waits while an audio edit or save is running.

### Installed voice previews

Select a voice in either installed-voice list and press `Ctrl+P` or `Alt+P` to play its sample. Press the shortcut again during playback to stop it. When playback finishes, focus returns to the same list, so you can select the next voice with an arrow key and press `Ctrl+P` again. Focus is not moved if you switched to another application or tab.

Use **Open voice folder** (`Alt+O`) to open the selected profile's containing folder in File Explorer. This works for user-installed and packaged profiles.

The **Page status** field reports loading for the selected tab. NVDA announces loading and completion while the manager is active.

Opening the manager starts loading XTTS in a background thread if its preview helper is not already running. You can select recordings while it loads and close the manager without waiting for startup. The loaded helper remains available for later cloning and synthesis until NVDA exits. Background loading does not generate a test sentence.

Installed-voice previews are cached as complete WAV files under `%APPDATA%\nvda\maxlogicXTTSv2\cache\preview-wav`. A replay with the same voice files, sample text, and language uses that file without starting the speech model. Changing the voice files or preview language produces a new cache entry. The first uncached preview still needs to load the XTTS model; after startup it streams audio as it becomes available. If playback is stopped, generation may finish in the background to complete the cached sample.

## Licenses

The add-on code is released under the MIT License. See `LICENSE`.

The XTTS v2 model is not part of the add-on package. The helper downloads it from Coqui on first use. It is released under the [Coqui Public Model License](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt), which allows **non-commercial use only**. The helper accepts that license for you when it downloads the model, so installing and using this add-on means you agree to it. Do not use XTTS voices from this add-on for commercial purposes.

Python packages, voice samples and catalog sources are listed in `THIRD-PARTY-NOTICES.md`.

## Add-on identity

- Add-on ID: `maxlogicXTTSv2`
- Synth driver: `maxlogic_xtts_v2`
- Display name: `MaxLogic XTTS v2`

## NVDA compatibility and checks

This release targets NVDA 2026.2. The minimum supported version is 2024.1.

Run the repository checks with `python -m unittest discover -s tests -v`. Runtime synthesis and physical keyboard/speech checks are separate from these tests.

The CUDA graph decoding test needs the real model and an NVIDIA GPU, so it runs only with the helper's Python: `.helper-venv\Scripts\python.exe -m unittest discover -s tests -p test_fast_gpt.py`. It checks that the graph picks the same tokens as Coqui in English and Polish.

### Clone Voice

Open **Clone Voice**, add one or more clear recordings of the same speaker, and choose the default language and presets. Use **Extract Sample** first when a recording needs trimming. **Clone for testing** (Alt+C) prepares a temporary voice without asking for a name or adding anything to Installed.

Use **Sample text** (Alt+T) to enter your own text, then **Play sample** (Alt+P). **Reset to default text** (Alt+D) restores the default for the selected language. Matching samples use the preview cache; new samples stream as they are generated.

Choose **Save voice** (Alt+S) when you want to keep the result. Enter a name, then confirm replacement if that voice already exists. Cancelling keeps your test clone available. Saving reuses the prepared conditioning. Closing the manager discards temporary test files; saved voices remain installed.

Choose **Delete installed voice** (Alt+O) to remove a cloned or other user-installed voice. Select the voice and confirm the deletion. Packaged voices are not offered for deletion.

Reference volume normalization is optional and off by default. **Show advanced settings** reveals:

- **Maximum seconds per recording** (default 30): how much of each recording XTTS reads.
- **Total conditioning seconds** (default 6): how much of the joined recordings is used for GPT conditioning.
- **Conditioning chunk seconds** (default 6): chunk size within that conditioning audio; it must not exceed the total.

Longer values do not guarantee better results. These settings affect voice conditioning; they are not speaking-speed or synthesis-temperature controls. Model startup can take tens of seconds. Cloning runs in a worker while a spinner appears on its button. NVDA announces completion using the current voice. The dialog grows to fit the cloning controls, including advanced settings. On a smaller screen, advanced settings open in a separate fitted dialog. After saving, the profile appears in Installed, where its folder can be opened.


### Choosing recordings and presets

Select one input in **Reference recordings**, then choose **Play selected recording** (Alt+I) to listen to the original audio. The same button stops playback. Input playback does not require the XTTS model and stops when you remove the recording, start a generated sample, or close the manager.

**Balance speech style across all recordings** (Alt+B, on by default) changes how the speech-style budget (Total conditioning seconds) is used. Stock XTTS joins the recordings and uses only the first seconds, so a short fragment listed first can take most of the budget and later recordings add no style. With balancing, each recording gets an equal share, taken from its middle; a recording shorter than its share passes the remainder to the others, and list order no longer matters. For example, with 12 seconds and recordings of 2.5 seconds, 19 minutes and 53 seconds, the shares are about 2.5, 4.8 and 4.8 seconds. Speaker identity is computed the same way in both modes. Clear this option to reproduce stock XTTS behavior.

**Check recordings** also reports how much of each file falls inside the speech-style conditioning window. Before cloning, a warning identifies recordings estimated to contribute no speech style and lets you return to the inputs or continue. These recordings still contribute to speaker identity. If edge trimming is enabled, the report is an estimate before trimming; shorter prepared inputs can allow later recordings into the window.

The Clone Voice tab has **Help: choosing recordings** (Alt+H) and **Check recordings** (Alt+K). The checker reports duration, channels and sample rate without loading XTTS. It suggests PCM WAV or FLAC conversion only if decoding fails; stereo or 48 kHz alone does not require conversion. XTTS handles mono mixing and resampling internally. Its output is 24 kHz.

Use clear recordings of one speaker with consistent sound and little edge silence. Roughly 6–15 seconds per clip is a starting point, not a quality guarantee. Several clean references may help, but more files are not automatically better than one good recording. Speaker embeddings are averaged; GPT conditioning uses the selected duration from joined recordings in list order. Optional edge trimming preserves original files, retains about 100 ms of margin and keeps internal pauses. It is off by default.

Reference conditioning presets use maximum-reference / total-conditioning / chunk seconds: XTTS model config **30 / 30 / 4** (default), Function defaults **30 / 6 / 6**, Extended **30 / 12 / 6**, Longer **30 / 30 / 6**. The model config values come from the released XTTS v2 `config.json` and are what Coqui's high-level synthesis uses; the function defaults are the bare `get_conditioning_latents()` keyword defaults. XTTS has no hard length limit: each chunk becomes a fixed-size style summary and the summaries are averaged, so longer totals mainly add averaging and time. The model was trained on 3 to 6 second chunks. Advanced controls remain editable.

Speech generation presets are separate and saved with each newly created voice:

| Preset | Temperature | Top p | Top k | Repetition penalty | Speed |
| --- | --- | --- | --- | --- | --- |
| XTTS model config (default) | 0.75 | 0.85 | 50 | 5 | 1.0 |
| XTTS inference function defaults | 0.75 | 0.85 | 50 | 10 | 1.0 |
| Suggested range: midpoint | 0.75 | 0.85 | 50 | 2 | 1.0 |
| Suggested range: lower | 0.65 | 0.80 | 50 | 2 | 1.0 |
| Suggested range: upper | 0.85 | 0.90 | 50 | 2 | 1.0 |

These are starting points for listening comparisons, not ranked quality presets. Speed multiplies NVDA's rate. Settings affect both streamed and buffered synthesis. Changing controls does not edit an existing voice. Saved conditioning avoids recloning, but generating new text still needs the model; a matching preview WAV does not. Cache identities include profile files and speech settings.

References checked: [Coqui package](https://pypi.org/project/coqui-tts/), [XTTS 0.27.5 source](https://github.com/idiap/coqui-ai-TTS/blob/v0.27.5/TTS/tts/models/xtts.py), [voice caching](https://coqui-tts.readthedocs.io/en/latest/cloning.html).
