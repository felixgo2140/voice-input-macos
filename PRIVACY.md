# Privacy

Voice Input runs locally on macOS and has no project-operated backend.

- Microphone capture starts only after the configured shortcut is pressed.
- Recorded audio is written to a temporary WAV file, sent to the ASR endpoint selected by the user, then deleted.
- The transcript is sent to the LLM endpoint selected by the user for cleanup or translation.
- API credentials are stored in a local file with owner-only `0600`
  permissions. This avoids login-password prompts across ad-hoc app updates.
- The app does not include analytics, advertising, crash reporting, or telemetry.
- UI text inspected through macOS Accessibility is used only to infer an output language and position the panel. It is not logged or persisted by the app.

The privacy policies and retention rules of the ASR and LLM providers selected by the user still apply.
