# Q760: Session fixation or stale-session reuse in WebAuthnID

## Question
Can an unprivileged attacker exploit session cookie plus API token on the same request at `core/sessions/webauthn.go:WebAuthnID` so `WebAuthnID` accepts a stale, fixed, or concurrently invalidated session/token pair, causing unauthorized access to API tokens, session state, or sensitive node configuration instead of preserving that session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal?

## Target
- File/function: core/sessions/webauthn.go::WebAuthnID
- Entrypoint: core/sessions/webauthn.go:WebAuthnID
- Attacker controls: session cookie plus API token on the same request
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal
- Expected Immunefi impact: unauthorized access to API tokens, session state, or sensitive node configuration
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
