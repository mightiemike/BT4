# Q1426: Payload-carrying inbound spawns the wrong outbound context via Source-Chain Fields Such As / Failed Inbound Should Still in Keeper.VoteInbound

## Question
Can an unprivileged attacker enter through a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound` with source-chain fields such as `tx_hash`, `log_index`, `sender`, `asset_addr`, `amount`, and `raw_payload` when a failed inbound should still preserve a safe recovery path, and cause `Keeper.VoteInbound` to push the wrong logical object through a vote or terminal state transition, so that it make execution from one inbound attach outbounds or rescue state to another logical transaction, breaking the invariant that outbounds must remain attached to the exact inbound that created them, and resulting in Direct loss or permanent freeze of bridged funds?

## Target
- File/function: x/uexecutor/keeper/msg_vote_inbound.go::Keeper.VoteInbound
- Entrypoint: a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound`
- Attacker controls: source-chain fields such as `tx_hash`, `log_index`, `sender`, `asset_addr`, `amount`, and `raw_payload`
- Exploit idea: Cause `Keeper.VoteInbound` to push the wrong logical object through a vote or terminal state transition, so it can make execution from one inbound attach outbounds or rescue state to another logical transaction.
- Invariant to test: outbounds must remain attached to the exact inbound that created them
- Expected Immunefi impact: Direct loss or permanent freeze of bridged funds
- Fast validation: write a keeper integration test that finalizes the crafted inbound(s) through the honest-UV path and inspect the UTX, PC tx, and recovery lifecycle
