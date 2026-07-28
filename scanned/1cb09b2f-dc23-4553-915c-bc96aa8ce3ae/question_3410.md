# Q3410: Observation canonicalization merges distinct destination results via Multiple Outbounds Emitted From / Terminal Status Should Be in Keeper.CreateUniversalTxFromPCTx

## Question
Can an unprivileged attacker enter through a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound` with multiple outbounds emitted from one payload or receipt when terminal status should be reached exactly once, and cause `Keeper.CreateUniversalTxFromPCTx` to bind a new record or derived action to the wrong live context, so that it shape tx-hash or error-formatting variants so honest observations of different outcomes land on one ballot, breaking the invariant that one outbound ballot must represent exactly one destination-chain outcome, and resulting in Wrong refund/revert handling leading to direct loss or permanent freeze?

## Target
- File/function: x/uexecutor/keeper/create_outbound.go::Keeper.CreateUniversalTxFromPCTx
- Entrypoint: a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound`
- Attacker controls: multiple outbounds emitted from one payload or receipt
- Exploit idea: Cause `Keeper.CreateUniversalTxFromPCTx` to bind a new record or derived action to the wrong live context, so it can shape tx-hash or error-formatting variants so honest observations of different outcomes land on one ballot.
- Invariant to test: one outbound ballot must represent exactly one destination-chain outcome
- Expected Immunefi impact: Wrong refund/revert handling leading to direct loss or permanent freeze
- Fast validation: write a keeper integration test that finalizes the crafted outbound observation and inspect status transitions, refund math, and remint behavior
