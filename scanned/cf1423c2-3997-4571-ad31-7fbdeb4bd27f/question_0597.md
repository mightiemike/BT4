# Q597: Cross-route auth context bleed through deleteStaleSessions

## Question
Can an unprivileged attacker trigger `deleteStaleSessions` from `core/sessions/oidcauth/reaper.go:deleteStaleSessions` with path/body identifiers bound to the current session so a low-privilege route inherits authorization state from a more-privileged one, causing unauthorized access to API tokens, session state, or sensitive node configuration and violating session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal?

## Target
- File/function: core/sessions/oidcauth/reaper.go::deleteStaleSessions
- Entrypoint: core/sessions/oidcauth/reaper.go:deleteStaleSessions
- Attacker controls: path/body identifiers bound to the current session
- Exploit idea: Drive mixed session/token/MFA states through the real auth stack and confirm whether identity, role, and throttling stay stable.
- Invariant to test: session invalidation, MFA state, and API-token issuance must stay bound to the same authenticated principal
- Expected Immunefi impact: unauthorized access to API tokens, session state, or sensitive node configuration
- Fast validation: Replay the minimal request under unauthenticated, low-privilege, and mixed auth contexts; assert the same principal, role, and rate-limit bucket are enforced end to end.
