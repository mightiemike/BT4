# Q1008: Boundary preservation edge case in getCurrentSessionID #2

## Question
Can an unprivileged attacker use concurrent password-update and API-token operations at `core/web/user_controller.go:getCurrentSessionID` so `getCurrentSessionID` reaches a concrete path to unauthorized access to sensitive user/session material by breaking the invariant that one user identifier must never resolve to another principal after normalization, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/user_controller.go::getCurrentSessionID
- Entrypoint: core/web/user_controller.go:getCurrentSessionID
- Attacker controls: concurrent password-update and API-token operations
- Exploit idea: Target the exact user/token/password mutation path and prove whether principal binding or revocation breaks under crafted identifiers or request ordering.
- Invariant to test: one user identifier must never resolve to another principal after normalization
- Expected Immunefi impact: unauthorized access to sensitive user/session material
- Fast validation: Run an integration test that mutates another user/token/password via crafted identifiers and concurrent requests; assert the operation never escapes the intended principal.
