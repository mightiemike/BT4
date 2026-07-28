# Q2416: Zero-amount payload inbound breaks deposit-before-execution assumptions via Source-Chain Gateway Event Attacker / Inbound Will Create Visible in MsgVoteInbound.GetSigners

## Question
Can an unprivileged attacker enter through a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound` with a source-chain gateway event the attacker can trigger through a normal deposit or bridge action when the inbound will create a visible UTX even if execution validation fails, and cause `MsgVoteInbound.GetSigners` to derive the wrong effective signer or omit the real principal, so that it use a zero-amount payload-capable tx type to execute logic that assumes funds or gas top-up happened first, breaking the invariant that payload execution must not obtain privileges or value effects that depend on a deposit that never occurred, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uexecutor/types/msg_vote_inbound.go::MsgVoteInbound.GetSigners
- Entrypoint: a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound`
- Attacker controls: a source-chain gateway event the attacker can trigger through a normal deposit or bridge action
- Exploit idea: Cause `MsgVoteInbound.GetSigners` to derive the wrong effective signer or omit the real principal, so it can use a zero-amount payload-capable tx type to execute logic that assumes funds or gas top-up happened first.
- Invariant to test: payload execution must not obtain privileges or value effects that depend on a deposit that never occurred
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper integration test that finalizes the crafted inbound(s) through the honest-UV path and inspect the UTX, PC tx, and recovery lifecycle
