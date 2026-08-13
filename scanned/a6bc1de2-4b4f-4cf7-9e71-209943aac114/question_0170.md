# Q170: Session fixation or stale-session reuse in SetPassword

## Question
Can an unprivileged attacker exploit session cookie plus API token on the same request at `core/sessions/ldapauth/ldap.go:SetPassword` so `SetPassword` accepts a stale, fixed, or concurrently invalidated session/token pair, causing unauthorized access to API tokens, session state, or sensitive node configuration instead of preserving that session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal?

## Target
- File/function: core/sessions/ldapauth/ldap.go::SetPassword
- Entrypoint: core/sessions/ldapauth/ldap.go:SetPassword
- Attacker controls: session cookie plus API token on the same request
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal
- Expected Immunefi impact: unauthorized access to API tokens, session state, or sensitive node configuration
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
