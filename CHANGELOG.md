# Changelog

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
