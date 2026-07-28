# Q1940: Unprivileged process initiation bypasses admin authority via Direct Utss Message Submission / Attacker Does Not Already in Params.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `utss` message submission against vote, process, or migration handlers with a direct `utss` message submission against vote, process, or migration handlers when the attacker does not already control a UV, admin, or governance key, and cause `Params.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it start or alter TSS key lifecycle from a non-admin path, breaking the invariant that only the configured authority should initiate key processes or migrations, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/utss/types/params.go::Params.ValidateBasic
- Entrypoint: a direct `utss` message submission against vote, process, or migration handlers
- Attacker controls: a direct `utss` message submission against vote, process, or migration handlers
- Exploit idea: Cause `Params.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can start or alter TSS key lifecycle from a non-admin path.
- Invariant to test: only the configured authority should initiate key processes or migrations
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and check whether the TSS vote or process state changes anyway
