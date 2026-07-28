# Q0120: Unprivileged chain-config creation bypasses authority via Params Updates Would Change / Attacker Does Not Already in Params.Validate

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the attacker does not already control admin or governance keys, and cause `Params.Validate` to trigger an unsafe state-transition edge case, so that it make an admin-gated registry write succeed from an unprivileged account, breaking the invariant that only the configured registry authority should mutate chain configuration, and resulting in Direct theft/loss or permanent freezing of funds by malicious reconfiguration?

## Target
- File/function: x/uregistry/types/params.go::Params.Validate
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `Params.Validate` to trigger an unsafe state-transition edge case, so it can make an admin-gated registry write succeed from an unprivileged account.
- Invariant to test: only the configured registry authority should mutate chain configuration
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds by malicious reconfiguration
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
