# Changelog

## 0.1.0

- forked the Kokoro NVDA add-on layout into a new `xtts-v2-nvda` repository
- replaced the Kokoro synth runtime with an XTTS v2 helper-first engine and XTTS reference-audio profile store
- kept the NVDA voice manager, cache controls, local install flow, sample preview, and catalog scaffolding
- added Hugging Face search with live XTTS sample preview and install from sample audio
- added direct `.pth` install support with compatibility validation
- added sample extraction from long recordings inside the voice manager
- added safe Extract Sample editing with temporary working copies, snippet deletion, and direct streaming playback for large WAV sources
- added Extract Sample keyboard shortcuts and live selection-length guidance
