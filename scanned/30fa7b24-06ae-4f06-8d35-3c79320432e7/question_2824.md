# Q2824: Swap-refund fallback refunds the wrong asset semantics via Cross-Chain Flow Makes Push / Terminal Status Should Be in Keeper.UpdateOutbound

## Question
Can an unprivileged attacker enter through a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound` with a cross-chain flow that makes Push Chain create one or more outbounds when terminal status should be reached exactly once, and cause `Keeper.UpdateOutbound` to overwrite a different live record than the caller should be able to affect, so that it force the refund path from swap mode into fallback mode under attacker-favorable parameters, breaking the invariant that refund fallback must preserve amount, asset, and recipient invariants across both modes, and resulting in Direct theft/loss or permanent freezing of funds?

## Target
- File/function: x/uexecutor/keeper/outbound.go::Keeper.UpdateOutbound
- Entrypoint: a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound`
- Attacker controls: a cross-chain flow that makes Push Chain create one or more outbounds
- Exploit idea: Cause `Keeper.UpdateOutbound` to overwrite a different live record than the caller should be able to affect, so it can force the refund path from swap mode into fallback mode under attacker-favorable parameters.
- Invariant to test: refund fallback must preserve amount, asset, and recipient invariants across both modes
- Expected Immunefi impact: Direct theft/loss or permanent freezing of funds
- Fast validation: write a keeper integration test that finalizes the crafted outbound observation and inspect status transitions, refund math, and remint behavior
