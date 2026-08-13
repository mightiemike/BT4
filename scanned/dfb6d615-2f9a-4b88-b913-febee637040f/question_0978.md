# Q978: Boundary preservation edge case in Index #4

## Question
Can an unprivileged attacker use path/body identity mismatches during user or token deletion at `GET /v2/users` so `Index` reaches a concrete path to unauthorized API token issuance or password change for another principal by breaking the invariant that current-session binding must not drift across password or token operations, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/user_controller.go::Index
- Entrypoint: GET /v2/users
- Attacker controls: path/body identity mismatches during user or token deletion
- Exploit idea: Target the exact user/token/password mutation path and prove whether principal binding or revocation breaks under crafted identifiers or request ordering.
- Invariant to test: current-session binding must not drift across password or token operations
- Expected Immunefi impact: unauthorized API token issuance or password change for another principal
- Fast validation: Run an integration test that mutates another user/token/password via crafted identifiers and concurrent requests; assert the operation never escapes the intended principal.
