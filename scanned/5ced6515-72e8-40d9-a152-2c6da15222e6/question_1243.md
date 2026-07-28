# Q1243: Swap-refund fallback refunds the wrong asset semantics via Outbound Whose Revert Instructions, / Destination Observation Drives Refund in Keeper.CreateUniversalTxFromPCTx

## Question
Can an unprivileged attacker enter through a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound` with an outbound whose revert instructions, gas token, or refund path are attacker-influenced through the originating action when the destination observation drives refund or revert logic, and cause `Keeper.CreateUniversalTxFromPCTx` to bind a new record or derived action to the wrong live context, so that it force the refund path from swap mode into fallback mode under attacker-favorable parameters, breaking the invariant that refund fallback must preserve amount, asset, and recipient invariants across both modes, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uexecutor/keeper/create_outbound.go::Keeper.CreateUniversalTxFromPCTx
- Entrypoint: a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound`
- Attacker controls: an outbound whose revert instructions, gas token, or refund path are attacker-influenced through the originating action
- Exploit idea: Cause `Keeper.CreateUniversalTxFromPCTx` to bind a new record or derived action to the wrong live context, so it can force the refund path from swap mode into fallback mode under attacker-favorable parameters.
- Invariant to test: refund fallback must preserve amount, asset, and recipient invariants across both modes
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper integration test that finalizes the crafted outbound observation and inspect status transitions, refund math, and remint behavior
