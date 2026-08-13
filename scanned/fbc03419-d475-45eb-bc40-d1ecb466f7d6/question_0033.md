# Q33: Boundary preservation edge case in ClearNonCurrentSessions #2

## Question
Can an unprivileged attacker use session cookie plus API token on the same request at `core/sessions/ldapauth/ldap.go:ClearNonCurrentSessions` so `ClearNonCurrentSessions` reaches a concrete path to rate limit violations with real security impact by breaking the invariant that rate limiting must not be bypassable by switching auth representations, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/sessions/ldapauth/ldap.go::ClearNonCurrentSessions
- Entrypoint: core/sessions/ldapauth/ldap.go:ClearNonCurrentSessions
- Attacker controls: session cookie plus API token on the same request
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: rate limiting must not be bypassable by switching auth representations
- Expected Immunefi impact: rate limit violations with real security impact
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
