# Security Policy

## Supported version

Security fixes are applied to the latest release.

## Reporting a vulnerability

Please open a private GitHub security advisory for this repository. Do not include API keys, access tokens, recordings, transcripts, or other personal data in an issue.

## Credential handling

The application stores API keys in macOS Keychain under the service `com.felix.voiceinput`. Plaintext keys from older local configurations are migrated to Keychain on startup and removed from the JSON file only after the Keychain write succeeds.

Repository releases never include a user configuration or API credentials.
