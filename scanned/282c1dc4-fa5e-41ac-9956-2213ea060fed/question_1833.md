# Q1833: Observation canonicalization merges distinct destination results via Destination-Chain Observation Fields Such / Destination Observation Drives Refund in Keeper.BuildOutboundsFromReceipt

## Question
Can an unprivileged attacker enter through a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound` with destination-chain observation fields such as `tx_hash`, `block_height`, `success`, `gas_fee_used`, and `error_msg` when the destination observation drives refund or revert logic, and cause `Keeper.BuildOutboundsFromReceipt` to bind a new record or derived action to the wrong live context, so that it shape tx-hash or error-formatting variants so honest observations of different outcomes land on one ballot, breaking the invariant that one outbound ballot must represent exactly one destination-chain outcome, and resulting in Wrong refund/revert handling leading to direct loss or permanent freeze?

## Target
- File/function: x/uexecutor/keeper/create_outbound.go::Keeper.BuildOutboundsFromReceipt
- Entrypoint: a user-controlled cross-chain flow whose outbound is later observed via `MsgVoteOutbound`
- Attacker controls: destination-chain observation fields such as `tx_hash`, `block_height`, `success`, `gas_fee_used`, and `error_msg`
- Exploit idea: Cause `Keeper.BuildOutboundsFromReceipt` to bind a new record or derived action to the wrong live context, so it can shape tx-hash or error-formatting variants so honest observations of different outcomes land on one ballot.
- Invariant to test: one outbound ballot must represent exactly one destination-chain outcome
- Expected Immunefi impact: Wrong refund/revert handling leading to direct loss or permanent freeze
- Fast validation: write a keeper integration test that finalizes the crafted outbound observation and inspect status transitions, refund math, and remint behavior
