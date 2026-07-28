# Q1301: Authz wrapping weakens registry authority checks via Direct Submission Of Admin- / Attacker Does Not Already in MsgUpdateTokenConfig.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a direct submission of an admin- or gov-gated `uregistry` message when the attacker does not already control admin or governance keys, and cause `MsgUpdateTokenConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it reach a registry mutation through a wrapper path the module does not intend to trust, breaking the invariant that wrappers must not relax the authority boundary for registry writes, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_update_token_config.go::MsgUpdateTokenConfig.ValidateBasic
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a direct submission of an admin- or gov-gated `uregistry` message
- Exploit idea: Cause `MsgUpdateTokenConfig.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can reach a registry mutation through a wrapper path the module does not intend to trust.
- Invariant to test: wrappers must not relax the authority boundary for registry writes
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
