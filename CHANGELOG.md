# Changelog

## 1.3.4 — 2026-07-30

- Keeps sounddevice's bundled PortAudio dynamic library outside the Python ZIP
  so recording can start in the packaged macOS application.
- Adds build-time audio loading verification and an optional real input-stream
  smoke test.

## 1.3.3 — 2026-07-29

- Replaces free-text ASR and LLM provider/model fields with linked dropdowns.
- Automatically configures the service endpoint for the selected provider.
- Preserves existing custom provider/model values as selectable legacy options.

## 1.3.2 — 2026-07-29

- Removes macOS Keychain access so updates never request the user's login
  password.
- Stores API credentials in an owner-only local file with `0600` permissions.

## 1.3.1 — 2026-07-29

- Keeps the application running when macOS Accessibility permission needs to
  be granted again after an update.
- Shows the settings/permission guidance instead of entering a LaunchAgent
  restart loop.

## 1.3.0 — 2026-07-29

First public release.

- Right Option starts and stops recording; Esc cancels.
- Streams speech recognition and LLM cleanup previews.
- Automatically returns to the original editor and pastes the final result.
- Does not intercept Enter or Shift + Enter.
- Keeps the floating panel stable across multiple displays and Spaces.
- Recovers from common CoreAudio device and sample-rate changes.
- Adds a native model/settings window and first-run permission guidance.
- Stores ASR and LLM credentials in macOS Keychain.
- Supports OpenAI-compatible ASR and chat-completion endpoints.
- Ships an Apple Silicon macOS application with an ad-hoc signature.
