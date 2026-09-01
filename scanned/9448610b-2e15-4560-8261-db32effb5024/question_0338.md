# Q0338: admission on a stale fork via `remove_transactions` (mempool.rs)

## Question
Can an unprivileged attacker who submits a raw EVM transaction to the public sequencer via `eth_sendRawTransaction`, controlling nonce, gas limit and `max_fee_per_gas`, drive `remove_transactions` in `crates/sequencer/src/mempool.rs` so that the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules, breaking the invariant that admission and execution agree on the active fork?

## Target
- File/function: `crates/sequencer/src/mempool.rs` -> `remove_transactions`
- Entrypoint: unprivileged party submits a raw EVM transaction to the public sequencer via `eth_sendRawTransaction`
- Attacker controls: nonce, gas limit and `max_fee_per_gas`
- Exploit idea: admission on a stale fork - reach `remove_transactions` from that entrypoint and force the divergence where the fork rules the validator applied and the fork rules in force at inclusion stop being the same rules; the adjacent symbols in the same file that carry the value are `CitreaMempool`, `add_external_transaction`, `get`, `all_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission and execution agree on the active fork
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: straddle an activation height and assert no admitted transaction becomes invalid
