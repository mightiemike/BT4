# Q974: Unauthorized user mutation in DeleteAPIToken

## Question
Can an unprivileged attacker use target email, role fields, current password, and current auth context at `POST /v2/user/token/delete` so `DeleteAPIToken` mutates another user’s role, password, or token state without the intended privilege boundary, leading to authentication bypass or privilege escalation into privileged node actions and violating user, role, password, and API-token mutations must apply only to the authenticated principal or explicitly authorized target?

## Target
- File/function: core/web/user_controller.go::DeleteAPIToken
- Entrypoint: POST /v2/user/token/delete
- Attacker controls: target email, role fields, current password, and current auth context
- Exploit idea: Target the exact user/token/password mutation path and prove whether principal binding or revocation breaks under crafted identifiers or request ordering.
- Invariant to test: user, role, password, and API-token mutations must apply only to the authenticated principal or explicitly authorized target
- Expected Immunefi impact: authentication bypass or privilege escalation into privileged node actions
- Fast validation: Run an integration test that mutates another user/token/password via crafted identifiers and concurrent requests; assert the operation never escapes the intended principal.
