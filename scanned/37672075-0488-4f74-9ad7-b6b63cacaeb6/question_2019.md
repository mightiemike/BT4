# Q2019: Normalization removes the field that distinguishes safe from unsafe execution via Inbound Whose Payload, Revert / Inbound Will Create Visible in Inbound.NormalizeForTxType

## Question
Can an unprivileged attacker enter through a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound` with an inbound whose payload, revert instructions, or recipient fields sit on edge-case boundaries when the inbound will create a visible UTX even if execution validation fails, and cause `Inbound.NormalizeForTxType` to collapse two security-relevant cases into one normalized form, so that it strip or rewrite a field so a malicious inbound survives into the wrong execution branch, breaking the invariant that normalization must preserve every field needed to keep authorization and asset semantics intact, and resulting in Direct theft/loss or unauthorized execution?

## Target
- File/function: x/uexecutor/types/inbound.go::Inbound.NormalizeForTxType
- Entrypoint: a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound`
- Attacker controls: an inbound whose payload, revert instructions, or recipient fields sit on edge-case boundaries
- Exploit idea: Cause `Inbound.NormalizeForTxType` to collapse two security-relevant cases into one normalized form, so it can strip or rewrite a field so a malicious inbound survives into the wrong execution branch.
- Invariant to test: normalization must preserve every field needed to keep authorization and asset semantics intact
- Expected Immunefi impact: Direct theft/loss or unauthorized execution
- Fast validation: write a keeper integration test that finalizes the crafted inbound(s) through the honest-UV path and inspect the UTX, PC tx, and recovery lifecycle
