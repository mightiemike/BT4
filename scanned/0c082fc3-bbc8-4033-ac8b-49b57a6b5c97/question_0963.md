# Q963: Concurrent password/token race in Delete

## Question
Can an unprivileged attacker abuse path/body identity mismatches during user or token deletion at `DELETE /v2/users/:email` so `Delete` leaves a privileged token or session valid after a password or role transition that should revoke it, causing authentication bypass or privilege escalation into privileged node actions and violating user, role, password, and API-token mutations must apply only to the authenticated principal or explicitly authorized target?

## Target
- File/function: core/web/user_controller.go::Delete
- Entrypoint: DELETE /v2/users/:email
- Attacker controls: path/body identity mismatches during user or token deletion
- Exploit idea: Target the exact user/token/password mutation path and prove whether principal binding or revocation breaks under crafted identifiers or request ordering.
- Invariant to test: user, role, password, and API-token mutations must apply only to the authenticated principal or explicitly authorized target
- Expected Immunefi impact: authentication bypass or privilege escalation into privileged node actions
- Fast validation: Run an integration test that mutates another user/token/password via crafted identifiers and concurrent requests; assert the operation never escapes the intended principal.
