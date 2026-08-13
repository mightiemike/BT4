# Q964: Identifier normalization mixup in Delete

## Question
Can an unprivileged attacker shape session/token ambiguity while mutating the current user at `DELETE /v2/users/:email` so `Delete` authorizes one user identifier but mutates or deletes another, leading to unauthorized access to sensitive user/session material and violating one user identifier must never resolve to another principal after normalization?

## Target
- File/function: core/web/user_controller.go::Delete
- Entrypoint: DELETE /v2/users/:email
- Attacker controls: session/token ambiguity while mutating the current user
- Exploit idea: Target the exact user/token/password mutation path and prove whether principal binding or revocation breaks under crafted identifiers or request ordering.
- Invariant to test: one user identifier must never resolve to another principal after normalization
- Expected Immunefi impact: unauthorized access to sensitive user/session material
- Fast validation: Run an integration test that mutates another user/token/password via crafted identifiers and concurrent requests; assert the operation never escapes the intended principal.
