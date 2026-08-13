# Changelog

## 1.4.0 — 2026-08-12

- Streams microphone frames to low-latency DashScope ASR while recording,
  instead of repeatedly uploading the complete WAV file.
- Falls back to Qwen `qwen3-asr-flash` automatically if the realtime stream
  is unavailable.
- Uses Qwen Model Studio `qwen-plus` with non-reasoning mode for substantially
  faster cleanup and translation; Qwen 3.8 and Kimi remain selectable.
- Makes the Write button finish an active recording, request writeback during
  processing, and retry the last result after automatic writeback.
- Preserves completed transcript segments across natural speaking pauses and
  supports 16 kHz and resampled device input.
- Adds packaged realtime pipeline diagnostics and end-to-end latency logging.

## 1.3.7 — 2026-08-12

- Makes Qwen Model Studio `qwen3-asr-flash` the default speech recognition
  model and Qwen Coding Plan `qwen3.8-max` the default cleanup model.
- Sends local WAV recordings to Qwen ASR as Base64 chat-audio input and
  preserves incremental transcript updates.
- Adds Kimi Coding Plan `k3` as an optional cleanup model and applies its
  required temperature value automatically.
- Keeps Qwen Model Studio, Qwen Coding Plan, and Kimi Coding Plan credentials
  separate because their API keys and endpoints are not interchangeable.
- Migrates legacy ASR and LLM credential slots without deleting old values.
- Shows whether the selected provider already has a saved credential.

## 1.3.6 — 2026-08-12

- Adds explicit “Start recording” and “Stop recording and process” menu items.
- Keeps recording available when a packaged Accessibility constant or optional
  caret position cannot be read.
- Resets the recording phase after unexpected start-up errors so Right Option
  cannot become stuck.
- Stops realtime preview after repeated or permanent service errors and limits
  a recording to ten minutes by default.
- Shows a concise message for speech-service quota errors.

## 1.3.5 — 2026-07-30

- Keeps soundfile's bundled libsndfile dynamic library outside the Python ZIP
  so recorded audio can be written and processed.
- Extends the packaged-app smoke test to write and read a WAV file in addition
  to opening a real microphone input stream.
- Shows concise guidance when a native audio component cannot load.

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
