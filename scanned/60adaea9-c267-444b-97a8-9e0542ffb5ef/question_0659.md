# Q659: Rate-limit bypass via auth representation churn in BeginWebAuthnLogin

## Question
Can an unprivileged attacker rotate or mix mixed GraphQL body, variables, aliases, and auth headers at `core/sessions/webauthn.go:BeginWebAuthnLogin` so `BeginWebAuthnLogin` materially bypasses intended login or auth throttling, causing authentication bypass or privilege escalation into privileged node actions and breaking authentication state must resolve to exactly one principal and role across all middleware and downstream handlers?

## Target
- File/function: core/sessions/webauthn.go::BeginWebAuthnLogin
- Entrypoint: core/sessions/webauthn.go:BeginWebAuthnLogin
- Attacker controls: mixed GraphQL body, variables, aliases, and auth headers
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: authentication state must resolve to exactly one principal and role across all middleware and downstream handlers
- Expected Immunefi impact: authentication bypass or privilege escalation into privileged node actions
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
