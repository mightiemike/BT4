# Q2423: Pending-outbound cleanup makes recovery impossible after partial failure via Multiple Outbounds Emitted From / Terminal Status Should Be in Keeper.AttachRescueOutboundFromReceipt

## Question
Can an unprivileged attacker enter through a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound` with multiple outbounds emitted from one payload or receipt when terminal status should be reached exactly once, and cause `Keeper.AttachRescueOutboundFromReceipt` to bind a new record or derived action to the wrong live context, so that it remove the pending index before the outbound has a durable, recoverable terminal state, breaking the invariant that every outbound must remain recoverable until one correct terminal outcome is durably stored, and resulting in Permanent freezing of funds?

## Target
- File/function: x/uexecutor/keeper/create_outbound.go::Keeper.AttachRescueOutboundFromReceipt
- Entrypoint: a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound`
- Attacker controls: multiple outbounds emitted from one payload or receipt
- Exploit idea: Cause `Keeper.AttachRescueOutboundFromReceipt` to bind a new record or derived action to the wrong live context, so it can remove the pending index before the outbound has a durable, recoverable terminal state.
- Invariant to test: every outbound must remain recoverable until one correct terminal outcome is durably stored
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: write a keeper integration test that finalizes the crafted outbound observation and inspect status transitions, refund math, and remint behavior
