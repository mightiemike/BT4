# Q942: Boundary preservation edge case in NewWebAuthnController #1

## Question
Can an unprivileged attacker use email normalization, password bytes, and MFA/WebAuthn challenge state at `core/web/webauthn_controller.go:NewWebAuthnController` so `NewWebAuthnController` reaches a concrete path to unauthorized access to API tokens, session state, or sensitive node configuration by breaking the invariant that session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/webauthn_controller.go::NewWebAuthnController
- Entrypoint: core/web/webauthn_controller.go:NewWebAuthnController
- Attacker controls: email normalization, password bytes, and MFA/WebAuthn challenge state
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal
- Expected Immunefi impact: unauthorized access to API tokens, session state, or sensitive node configuration
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
