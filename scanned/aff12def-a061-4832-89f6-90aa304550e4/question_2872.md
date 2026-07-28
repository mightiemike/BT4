# Q2872: Authz wrapping weakens registry authority checks via Direct Submission Of Admin- / Malicious Config Would Redirect in MsgUpdateChainConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a direct submission of an admin- or gov-gated `uregistry` message when a malicious config would redirect value or strand it, and cause `MsgUpdateChainConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it reach a registry mutation through a wrapper path the module does not intend to trust, breaking the invariant that wrappers must not relax the authority boundary for registry writes, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uregistry/types/msg_update_chain_config.go::MsgUpdateChainConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a direct submission of an admin- or gov-gated `uregistry` message
- Exploit idea: Cause `MsgUpdateChainConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can reach a registry mutation through a wrapper path the module does not intend to trust.
- Invariant to test: wrappers must not relax the authority boundary for registry writes
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
