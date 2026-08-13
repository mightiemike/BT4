# Q657: Cross-route auth context bleed through BeginWebAuthnLogin

## Question
Can an unprivileged attacker trigger `BeginWebAuthnLogin` from `core/sessions/webauthn.go:BeginWebAuthnLogin` with path/body identifiers bound to the current session so a low-privilege route inherits authorization state from a more-privileged one, causing unauthorized access to API tokens, session state, or sensitive node configuration and violating session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal?

## Target
- File/function: core/sessions/webauthn.go::BeginWebAuthnLogin
- Entrypoint: core/sessions/webauthn.go:BeginWebAuthnLogin
- Attacker controls: path/body identifiers bound to the current session
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal
- Expected Immunefi impact: unauthorized access to API tokens, session state, or sensitive node configuration
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
