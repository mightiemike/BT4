# Q999: Boundary preservation edge case in UpdateRole #1

## Question
Can an unprivileged attacker use target email, role fields, current password, and current auth context at `PATCH /v2/users` so `UpdateRole` reaches a concrete path to unauthorized API token issuance or password change for another principal by breaking the invariant that current-session binding must not drift across password or token operations, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/user_controller.go::UpdateRole
- Entrypoint: PATCH /v2/users
- Attacker controls: target email, role fields, current password, and current auth context
- Exploit idea: Target the exact user/token/password mutation path and prove whether principal binding or revocation breaks under crafted identifiers or request ordering.
- Invariant to test: current-session binding must not drift across password or token operations
- Expected Immunefi impact: unauthorized API token issuance or password change for another principal
- Fast validation: Run an integration test that mutates another user/token/password via crafted identifiers and concurrent requests; assert the operation never escapes the intended principal.
