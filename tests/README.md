Run `python -m unittest discover -s tests -v` from the repository root.

- The preview-startup regression holds the helper startup lock while requesting another warmup and verifies the caller remains responsive.
- The cache-helper test starts the real helper with `python -S` and isolated application data, then checks its ready and empty-cache responses without the synthesis dependencies.

These tests do not download models or verify generated audio. Full synthesis requires a configured XTTS helper and a reference voice.
