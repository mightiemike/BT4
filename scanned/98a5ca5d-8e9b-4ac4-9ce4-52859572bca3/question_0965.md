# Q965: Session-bound token confusion in Delete

## Question
Can an unprivileged attacker exploit concurrent password-update and API-token operations at `DELETE /v2/users/:email` so `Delete` mints or deletes API tokens for a different principal than the authenticated one, causing unauthorized API token issuance or password change for another principal and breaking current-session binding must not drift across password or token operations?

## Target
- File/function: core/web/user_controller.go::Delete
- Entrypoint: DELETE /v2/users/:email
- Attacker controls: concurrent password-update and API-token operations
- Exploit idea: Target the exact user/token/password mutation path and prove whether principal binding or revocation breaks under crafted identifiers or request ordering.
- Invariant to test: current-session binding must not drift across password or token operations
- Expected Immunefi impact: unauthorized API token issuance or password change for another principal
- Fast validation: Run an integration test that mutates another user/token/password via crafted identifiers and concurrent requests; assert the operation never escapes the intended principal.
