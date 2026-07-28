# Q2678: Signer derivation and declared authority can be split via Chain Token Config Payloads / Config Change Would Immediately in MsgUpdateParams.ValidateBasic

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with chain and token config payloads that would be dangerous if accepted from an unprivileged account when the config change would immediately affect live user flows if accepted, and cause `MsgUpdateParams.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it make the message look authorized while the actual signer is different, breaking the invariant that registry message authority checks must bind the signer to the declared authority field exactly, and resulting in Unprivileged takeover of registry control leading to fund loss?

## Target
- File/function: x/uregistry/types/msg_update_params.go::MsgUpdateParams.ValidateBasic
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: chain and token config payloads that would be dangerous if accepted from an unprivileged account
- Exploit idea: Cause `MsgUpdateParams.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can make the message look authorized while the actual signer is different.
- Invariant to test: registry message authority checks must bind the signer to the declared authority field exactly
- Expected Immunefi impact: Unprivileged takeover of registry control leading to fund loss
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
