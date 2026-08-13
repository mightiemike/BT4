# Q848: MFA/WebAuthn binding bug in GetAuthenticatedUser

## Question
Can an unprivileged attacker manipulate concurrent login/logout/token-minting requests at `core/web/auth/auth.go:GetAuthenticatedUser` so `GetAuthenticatedUser` binds MFA/WebAuthn state to the wrong login attempt or principal, leading to rate limit violations with real security impact and violating rate limiting must not be bypassable by switching auth representations?

## Target
- File/function: core/web/auth/auth.go::GetAuthenticatedUser
- Entrypoint: core/web/auth/auth.go:GetAuthenticatedUser
- Attacker controls: concurrent login/logout/token-minting requests
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: rate limiting must not be bypassable by switching auth representations
- Expected Immunefi impact: rate limit violations with real security impact
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
