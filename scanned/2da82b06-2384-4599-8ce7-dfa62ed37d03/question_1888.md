# Q1888: deposit body decoded twice differently via `add_deposit_tx` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan, controlling submission timing relative to block sealing, drive `add_deposit_tx` in `crates/sequencer/src/deposit_data_mempool.rs` so that the deposit body the simulation decodes and the body the block execution decodes stop being the same structure, breaking the invariant that one blob has one decoding?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `add_deposit_tx`
- Entrypoint: unprivileged party submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan
- Attacker controls: submission timing relative to block sealing
- Exploit idea: deposit body decoded twice differently - reach `add_deposit_tx` from that entrypoint and force the divergence where the deposit body the simulation decodes and the body the block execution decodes stop being the same structure; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `remove_deposits`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: one blob has one decoding
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: craft a body with two valid decodings and assert one is chosen
