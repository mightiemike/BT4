# Q1045: Status transitions allow double refund or double remint via Cross-Chain Flow Makes Push / Terminal Status Should Be in Keeper.BuildOutboundsFromReceipt

## Question
Can an unprivileged attacker enter through a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound` with a cross-chain flow that makes Push Chain create one or more outbounds when terminal status should be reached exactly once, and cause `Keeper.BuildOutboundsFromReceipt` to bind a new record or derived action to the wrong live context, so that it move an outbound through observed, reverted, aborted, or refund-applied states more than once, breaking the invariant that each outbound should permit at most one terminal refund or remint outcome, and resulting in Direct theft/loss of funds?

## Target
- File/function: x/uexecutor/keeper/create_outbound.go::Keeper.BuildOutboundsFromReceipt
- Entrypoint: a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound`
- Attacker controls: a cross-chain flow that makes Push Chain create one or more outbounds
- Exploit idea: Cause `Keeper.BuildOutboundsFromReceipt` to bind a new record or derived action to the wrong live context, so it can move an outbound through observed, reverted, aborted, or refund-applied states more than once.
- Invariant to test: each outbound should permit at most one terminal refund or remint outcome
- Expected Immunefi impact: Direct theft/loss of funds
- Fast validation: write a keeper integration test that finalizes the crafted outbound observation and inspect status transitions, refund math, and remint behavior
