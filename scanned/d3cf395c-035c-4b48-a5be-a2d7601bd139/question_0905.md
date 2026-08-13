# Q905: Boundary preservation edge case in NewSessionsController #4

## Question
Can an unprivileged attacker use mixed GraphQL body, variables, aliases, and auth headers at `core/web/sessions_controller.go:NewSessionsController` so `NewSessionsController` reaches a concrete path to unauthorized access to API tokens, session state, or sensitive node configuration by breaking the invariant that session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/sessions_controller.go::NewSessionsController
- Entrypoint: core/web/sessions_controller.go:NewSessionsController
- Attacker controls: mixed GraphQL body, variables, aliases, and auth headers
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal
- Expected Immunefi impact: unauthorized access to API tokens, session state, or sensitive node configuration
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
