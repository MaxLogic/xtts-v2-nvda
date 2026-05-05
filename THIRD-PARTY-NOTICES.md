# Third-Party Notices

MaxLogic XTTS v2 bundles add-on code under the MIT license.

Runtime dependencies and model assets may be installed or downloaded separately:

- Coqui TTS Python package
  - Source: https://pypi.org/project/coqui-tts/
  - License: MPL-2.0
- XTTS v2 model family
  - Source: https://docs.coqui.ai/en/dev/models/xtts.html
  - Model license: Coqui Public Model License
- OHF Voice bundled and catalog voice samples
  - Source: https://github.com/OHF-Voice/voice-datasets
  - License: CC0-1.0
  - Bundled profiles: `de_de_kerstin`, `en_us_joe`, `en_us_kathleen`, `pl_pl_darkman`, `pl_pl_gosia`
- Thorsten-Voice community catalog samples
  - Source: https://github.com/thorstenMueller/Thorsten-Voice
  - License: CC0-1.0
- Kyutai community catalog samples
  - Source: https://huggingface.co/kyutai/tts-voices
  - Licenses used in the catalog: CC0-1.0 and CC-BY-4.0
- Hugging Face search tab
  - Source: https://huggingface.co/
  - Behavior: searches live Hugging Face repos and installs from sample audio exposed by those repos
  - License handling: licenses vary by repo and are shown in the search results and stored in installed profile metadata when available

This repository does not include the full XTTS model payload by default.
