# Q1091: Signer derivation and declared authority can be split via Chain Token Config Payloads / Malicious Config Would Redirect in Keeper.UpdateParams

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with chain and token config payloads that would be dangerous if accepted from an unprivileged account when a malicious config would redirect value or strand it, and cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so that it make the message look authorized while the actual signer is different, breaking the invariant that registry message authority checks must bind the signer to the declared authority field exactly, and resulting in Unprivileged takeover of registry control leading to fund loss?

## Target
- File/function: x/uregistry/keeper/msg_update_params.go::Keeper.UpdateParams
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: chain and token config payloads that would be dangerous if accepted from an unprivileged account
- Exploit idea: Cause `Keeper.UpdateParams` to overwrite a different live record than the caller should be able to affect, so it can make the message look authorized while the actual signer is different.
- Invariant to test: registry message authority checks must bind the signer to the declared authority field exactly
- Expected Immunefi impact: Unprivileged takeover of registry control leading to fund loss
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
