# Q2870: Authz wrapping weakens registry authority checks via Chain Token Config Payloads / Malicious Config Would Redirect in MsgRemoveTokenConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with chain and token config payloads that would be dangerous if accepted from an unprivileged account when a malicious config would redirect value or strand it, and cause `MsgRemoveTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it reach a registry mutation through a wrapper path the module does not intend to trust, breaking the invariant that wrappers must not relax the authority boundary for registry writes, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_remove_token_config.go::MsgRemoveTokenConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: chain and token config payloads that would be dangerous if accepted from an unprivileged account
- Exploit idea: Cause `MsgRemoveTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can reach a registry mutation through a wrapper path the module does not intend to trust.
- Invariant to test: wrappers must not relax the authority boundary for registry writes
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
