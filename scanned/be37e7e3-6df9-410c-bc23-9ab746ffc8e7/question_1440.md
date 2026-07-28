# Q1440: Rescue or revert attachment lands on the wrong UTX via Multiple Outbounds Emitted From / Terminal Status Should Be in Keeper.CreateUniversalTxFromPCTx

## Question
Can an unprivileged attacker enter through a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound` with multiple outbounds emitted from one payload or receipt when terminal status should be reached exactly once, and cause `Keeper.CreateUniversalTxFromPCTx` to bind a new record or derived action to the wrong live context, so that it make receipt-derived rescue or revert logic attach to a different transaction record, breaking the invariant that receipt-derived outbounds must stay bound to the exact originating UTX and PC tx, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uexecutor/keeper/create_outbound.go::Keeper.CreateUniversalTxFromPCTx
- Entrypoint: a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound`
- Attacker controls: multiple outbounds emitted from one payload or receipt
- Exploit idea: Cause `Keeper.CreateUniversalTxFromPCTx` to bind a new record or derived action to the wrong live context, so it can make receipt-derived rescue or revert logic attach to a different transaction record.
- Invariant to test: receipt-derived outbounds must stay bound to the exact originating UTX and PC tx
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper integration test that finalizes the crafted outbound observation and inspect status transitions, refund math, and remint behavior
