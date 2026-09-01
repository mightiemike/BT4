# Q0748: admission on a stale fork via `add_external_transaction` (mempool.rs)

## Question
Can an unprivileged attacker who submits a transaction whose balance sits exactly at the L1-fee reservation boundary, controlling nonce, gas limit and `max_fee_per_gas`, drive `add_external_transaction` in `crates/sequencer/src/mempool.rs` so that the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules, breaking the invariant that admission and execution agree on the active fork?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `add_external_transaction`
- Entrypoint: unprivileged party submits a transaction whose balance sits exactly at the L1-fee reservation boundary
- Attacker controls: nonce, gas limit and `max_fee_per_gas`
- Exploit idea: admission on a stale fork - reach `add_external_transaction` from that entrypoint and force the divergence where the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `get`, `all_transactions`, `remove_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission and execution agree on the active fork
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: straddle an activation height and assert no admitted transaction becomes invalid
