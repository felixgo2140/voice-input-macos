# Contributing

1. Create a branch from `main`.
2. Keep credentials and local `config.json` out of commits.
3. Run:

   ```bash
   python3 -m unittest discover -s tests -v
   python3 -m py_compile voice_input.py voice_input_core.py macos_context.py keychain_store.py settings_window.py setup_app.py
   ```

4. Describe macOS version, display layout, target application, and permission state when reporting input-focus bugs.

Changes involving microphone capture, Accessibility, clipboard behavior, Keychain, signing, or notarization should include a focused regression test or a manual verification checklist.
