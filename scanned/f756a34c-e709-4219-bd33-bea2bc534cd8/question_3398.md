# Q3398: Ballot identity collision finalizes the wrong inbound variant via Inbound Whose Payload, Revert / Failed Inbound Should Still in Inbound.NormalizeForTxType

## Question
Can an unprivileged attacker enter through a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound` with an inbound whose payload, revert instructions, or recipient fields sit on edge-case boundaries when a failed inbound should still preserve a safe recovery path, and cause `Inbound.NormalizeForTxType` to collapse two security-relevant cases into one normalized form, so that it make honest UVs converge on one ballot for two semantically different inbounds, breaking the invariant that one ballot key must represent only one execution-relevant inbound meaning, and resulting in Direct theft/loss of funds or permanent freeze after wrong finalization?

## Target
- File/function: x/uexecutor/types/inbound.go::Inbound.NormalizeForTxType
- Entrypoint: a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound`
- Attacker controls: an inbound whose payload, revert instructions, or recipient fields sit on edge-case boundaries
- Exploit idea: Cause `Inbound.NormalizeForTxType` to collapse two security-relevant cases into one normalized form, so it can make honest UVs converge on one ballot for two semantically different inbounds.
- Invariant to test: one ballot key must represent only one execution-relevant inbound meaning
- Expected Immunefi impact: Direct theft/loss of funds or permanent freeze after wrong finalization
- Fast validation: write a keeper integration test that finalizes the crafted inbound(s) through the honest-UV path and inspect the UTX, PC tx, and recovery lifecycle
