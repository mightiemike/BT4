# Q0855: Pending-outbound cleanup makes recovery impossible after partial failure via Outbound Whose Revert Instructions, / Outbound Is Value-Bearing Refund-Bearing in Keeper.applyGasRefund

## Question
Can an unprivileged attacker enter through a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound` with an outbound whose revert instructions, gas token, or refund path are attacker-influenced through the originating action when the outbound is value-bearing or refund-bearing, and cause `Keeper.applyGasRefund` to charge, refund, or allocate the wrong amount or wrong asset semantics, so that it remove the pending index before the outbound has a durable, recoverable terminal state, breaking the invariant that every outbound must remain recoverable until one correct terminal outcome is durably stored, and resulting in Permanent freezing of funds?

## Target
- File/function: x/uexecutor/keeper/outbound.go::Keeper.applyGasRefund
- Entrypoint: a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound`
- Attacker controls: an outbound whose revert instructions, gas token, or refund path are attacker-influenced through the originating action
- Exploit idea: Cause `Keeper.applyGasRefund` to charge, refund, or allocate the wrong amount or wrong asset semantics, so it can remove the pending index before the outbound has a durable, recoverable terminal state.
- Invariant to test: every outbound must remain recoverable until one correct terminal outcome is durably stored
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: write a keeper integration test that finalizes the crafted outbound observation and inspect status transitions, refund math, and remint behavior
