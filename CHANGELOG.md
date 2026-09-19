# Changelog

## Unreleased

- XTTS speaks about three times faster on an NVIDIA GPU: each decoding step replays as one CUDA graph instead of about 700 separate GPU calls. Uncached speech starts after about 0.26 s instead of 0.7 s and no longer stutters. It picks exactly the tokens Coqui's own decoding picks. Set `MAXLOGIC_XTTS_V2_CUDA_GRAPH=0` to turn it off; if the graph fails, XTTS falls back to Coqui's decoding by itself.
- A voice says "Loading X T T S" when XTTS starts loading, "Still loading, please wait" every 10 seconds while it loads, and "X T T S is ready" when it can speak. Speech waits until the ready sound ends. The new Loading Sounds tab in the voice manager turns each sound off, replaces it with another WAV file, and sets how often the still loading sound repeats.
- Selecting XTTS, or starting NVDA with it, no longer freezes NVDA while the model loads (35 to 60 s). Speech starts once the model is ready.
- A long line no longer stops after its first words. The rest used to play only once it was synthesized in full, which takes several seconds; now it plays as the helper streams it. Set `MAXLOGIC_XTTS_V2_LIVE_STREAM_PLAYBACK=0` to go back to whole chunks.
- Text goes to XTTS in whole sentences, as many as fit under its limit for the language (80% of it, 200 characters for English), instead of a few words followed by the rest. Each extra request cost a short silence, and the split broke the sentence melody.
- Say-all no longer pauses before every line and sentence. The next sentence is synthesized while the current one plays.
- Speech that is sent while earlier speech still plays is queued instead of cutting it off.
- Interrupted speech also stops in the helper, so the next thing you hear starts sooner. Audio from interrupted speech is never played.
- Changing the voice in the settings ring no longer freezes NVDA until the helper answers.
- Operations cut short when NVDA exits say "The operation was interrupted" instead of "list index out of range".
- Clone and save failures are written to the NVDA log.
- A damaged speech cache is set aside and recreated instead of turning caching off.
- `helper.log` moves to `helper.old.log` once it passes 2 MB.
- Catalog updates in a new add-on version are shown; a cached copy of an older bundled catalog no longer hides them.
- The voice manager keeps the loaded model when voices are installed or removed, and frees it 10 minutes after the manager closes.
- The voice manager speaks success messages instead of showing a dialog to dismiss. A dialog still appears when NVDA must be restarted.
- Every control on the voice manager pages has its own access key, and focus stays where it was when a page reloads.
- The voice manager logs why a voice list, catalog or search failed to load.

## 0.1.7

- Fix Close, window X and Escape; add Ctrl+F4 to close the manager.
- Start loading XTTS in the background when the manager opens, reusing an existing helper.
- Play and stop selected input recordings on Clone Voice.
- Show reference contribution to speech-style conditioning and warn before cloning when recordings fall outside its window.
- Add Balance speech style (on by default): the style conditioning budget is shared across all recordings, taken from the middle of each, instead of only the start of the joined audio.
- Default to the released XTTS v2 model config: 30 / 30 / 4 second conditioning and repetition penalty 5. The previous defaults remain available as Function defaults presets.
- Keep reference order in the conditioning cache key, because stock XTTS conditioning depends on it. Existing cached conditioning is rebuilt once.

## 0.1.6

- Clone temporary test voices without naming or installing them; save explicitly and confirm replacements.
- Preserve the previous voice if replacement fails.
- Add custom sample text and a reset button; stream and cache test samples.
- Fit the Clone Voice form without a scrollbox, including advanced settings.
- Show button spinners during long operations and announce completion through NVDA.

## 0.1.5

- Update the tested helper stack to coqui-tts 0.27.5 and Transformers 4.57.6.
- Add accessible recording help, format checks and optional edge-silence trimming.
- Add separate conditioning and speech generation presets, applied to saved profiles.
- Include voice content and generation settings in speech cache identities.

## 0.1.4

- Add a dedicated Clone Voice tab with multiple reference recordings, a name and default language.
- Save computed voice conditioning before publishing the profile; keep existing voices on failure or duplicate names.
- Add reference normalization and advanced reference/conditioning length settings, plus a preview of the created voice.

## 0.1.3

- Show and announce the selected page's loading state and completion.
- Add Open voice folder and Ctrl+P for sample playback on Installed Voices.
- Return focus to the originating voice list after a sample finishes.
- Stream uncached installed-voice previews and reuse complete preview WAV files on disk.
- Distinguish cached playback, speech-engine startup, and sample generation in status feedback.

## 0.1.2

- Fix manager startup blocking, load optional pages on demand, and improve keyboard access for NVDA 2026.2.
- Keep helper startup locks off the NVDA UI thread and start preview synthesis only when needed.
- Run slow manager operations in workers; defer optional pages until selected.
- Add readable status fields, accessible voice checklists, scrolling sample controls, and safer deletion confirmations.
- Let cache-only helpers start without NumPy or the synthesis engine.

## 0.1.1

- Tested with NVDA 2026.1 and updated add-on compatibility metadata.

## 0.1.0

- forked the Kokoro NVDA add-on layout into a new `xtts-v2-nvda` repository
- replaced the Kokoro synth runtime with an XTTS v2 helper-first engine and XTTS reference-audio profile store
- kept the NVDA voice manager, cache controls, local install flow, sample preview, and catalog scaffolding
- added Hugging Face search with live XTTS sample preview and install from sample audio
- added direct `.pth` install support with compatibility validation
- added sample extraction from long recordings inside the voice manager
- added safe Extract Sample editing with temporary working copies, snippet deletion, and direct streaming playback for large WAV sources
- added Extract Sample keyboard shortcuts and live selection-length guidance
- added Extract Sample exports for the current marked snippet and the edited temporary audio copy
- added `.m4a` Extract Sample support by decoding editable sources to temporary WAV working files
- made the Extract Sample current-position field editable for direct seeking
- added toggle-to-stop selection preview and 3-second auditions before/from start and before/after end markers
- fixed installed-voice preview after adding or removing voices by refreshing the preview helper voice list
- made the XTTS synth driver visible in NVDA's synthesizer selector whenever the helper runtime is available
- fixed XTTS speech stalls by keeping the warmed helper process alive during normal NVDA speech cancellation
- improved XTTS runtime responsiveness by using neutral speed at NVDA rate 50, avoiding duplicate waveform time-scaling, and keeping voice conditioning tensors on the model device
- reduced the first XTTS playback chunk size so uncached speech can start sooner and stale focus-change requests block for less time
- pinned the helper runtime to the tested Coqui 0.24.3 / PyTorch 2.11 stack and added streamed helper synthesis for lower first-audio latency on uncached speech
- changed NVDA playback to coalesce helper stream chunks before feeding `nvwave` by default, avoiding silent or unstable playback from rapidly canceled live stream chunks
- consolidated Hugging Face, Official, and Community voice acquisition into one Browse Voices tab with an internal source selector
