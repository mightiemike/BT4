# Q2663: Signer derivation and declared authority can be split via Params Updates Would Change / Attacker Does Not Already in msgServer.UpdateChainConfig

## Question
Can an unprivileged attacker enter through a direct `uregistry` message submission against an authority-gated path with params updates that would change who controls registry writes when the attacker does not already control admin or governance keys, and cause `msgServer.UpdateChainConfig` to overwrite a different live record than the caller should be able to affect, so that it make the message look authorized while the actual signer is different, breaking the invariant that registry message authority checks must bind the signer to the declared authority field exactly, and resulting in Unprivileged takeover of registry control leading to fund loss?

## Target
- File/function: x/uregistry/keeper/msg_server.go::msgServer.UpdateChainConfig
- Entrypoint: a direct `uregistry` message submission against an authority-gated path
- Attacker controls: params updates that would change who controls registry writes
- Exploit idea: Cause `msgServer.UpdateChainConfig` to overwrite a different live record than the caller should be able to affect, so it can make the message look authorized while the actual signer is different.
- Invariant to test: registry message authority checks must bind the signer to the declared authority field exactly
- Expected Immunefi impact: Unprivileged takeover of registry control leading to fund loss
- Fast validation: write a message-server test from an unprivileged signer and assert whether the registry mutation commits or can be wrapped to commit
