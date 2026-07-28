# Q2020: Normalization removes the field that distinguishes safe from unsafe execution via Source-Chain Gateway Event Attacker / Failed Inbound Should Still in Inbound.ValidateBasic

## Question
Can an unprivileged attacker enter through a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound` with a source-chain gateway event the attacker can trigger through a normal deposit or bridge action when a failed inbound should still preserve a safe recovery path, and cause `Inbound.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so that it strip or rewrite a field so a malicious inbound survives into the wrong execution branch, breaking the invariant that normalization must preserve every field needed to keep authorization and asset semantics intact, and resulting in Direct theft/loss or unauthorized execution?

## Target
- File/function: x/uexecutor/types/inbound.go::Inbound.ValidateBasic
- Entrypoint: a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound`
- Attacker controls: a source-chain gateway event the attacker can trigger through a normal deposit or bridge action
- Exploit idea: Cause `Inbound.ValidateBasic` to accept a value whose later canonical or decoded form changes its security identity, so it can strip or rewrite a field so a malicious inbound survives into the wrong execution branch.
- Invariant to test: normalization must preserve every field needed to keep authorization and asset semantics intact
- Expected Immunefi impact: Direct theft/loss or unauthorized execution
- Fast validation: write a keeper integration test that finalizes the crafted inbound(s) through the honest-UV path and inspect the UTX, PC tx, and recovery lifecycle
