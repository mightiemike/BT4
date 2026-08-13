# Q31: Auth parser differential in ClearNonCurrentSessions

## Question
Can an unprivileged attacker use email normalization, password bytes, and MFA/WebAuthn challenge state at `core/sessions/ldapauth/ldap.go:ClearNonCurrentSessions` so `ClearNonCurrentSessions` authenticates one identity or role while downstream handlers act on another, leading to authentication bypass or privilege escalation into privileged node actions and breaking the invariant that authentication state must resolve to exactly one principal and role across all middleware and downstream handlers?

## Target
- File/function: core/sessions/ldapauth/ldap.go::ClearNonCurrentSessions
- Entrypoint: core/sessions/ldapauth/ldap.go:ClearNonCurrentSessions
- Attacker controls: email normalization, password bytes, and MFA/WebAuthn challenge state
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: authentication state must resolve to exactly one principal and role across all middleware and downstream handlers
- Expected Immunefi impact: authentication bypass or privilege escalation into privileged node actions
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
