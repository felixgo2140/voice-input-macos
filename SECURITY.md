# Security Policy

## Supported version

Security fixes are applied to the latest release.

## Reporting a vulnerability

Please open a private GitHub security advisory for this repository. Do not include API keys, access tokens, recordings, transcripts, or other personal data in an issue.

## Credential handling

The application stores API keys in
`~/Library/Application Support/VoiceInput/credentials.json` with owner-only
`0600` permissions. Plaintext keys from older local configurations are moved
to that file on startup and removed from the ordinary configuration only after
the private credential write succeeds. This design deliberately avoids macOS
login-password prompts when an ad-hoc signed build is updated.

Repository releases never include a user configuration or API credentials.
