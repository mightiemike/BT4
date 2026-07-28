# Q1097: Signer derivation and declared authority can be split via Direct Submission Of Admin- / Malicious Config Would Redirect in MsgRemoveTokenConfig.GetSigners

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with a direct submission of an admin- or gov-gated `uregistry` message when a malicious config would redirect value or strand it, and cause `MsgRemoveTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so that it make the message look authorized while the actual signer is different, breaking the invariant that registry message authority checks must bind the signer to the declared authority field exactly, and resulting in Unprivileged takeover of registry control leading to fund loss?

## Target
- File/function: x/uregistry/types/msg_remove_token_config.go::MsgRemoveTokenConfig.GetSigners
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: a direct submission of an admin- or gov-gated `uregistry` message
- Exploit idea: Cause `MsgRemoveTokenConfig.GetSigners` to derive the wrong effective signer or omit the real principal, so it can make the message look authorized while the actual signer is different.
- Invariant to test: registry message authority checks must bind the signer to the declared authority field exactly
- Expected Immunefi impact: Unprivileged takeover of registry control leading to fund loss
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
