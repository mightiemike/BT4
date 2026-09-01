# Q1338: deposit body decoded twice differently via `calc_tx_id` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan, controlling the ABI encoding of the wrapped Bridge argument, drive `calc_tx_id` in `crates/sequencer/src/deposit_data_mempool.rs` so that the deposit body the simulation decodes and the body the block execution decodes stop being the same structure, breaking the invariant that one blob has one decoding?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `calc_tx_id`
- Entrypoint: unprivileged party submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan
- Attacker controls: the ABI encoding of the wrapped Bridge argument
- Exploit idea: deposit body decoded twice differently - reach `calc_tx_id` from that entrypoint and force the divergence where the deposit body the simulation decodes and the body the block execution decodes stop being the same structure; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `remove_deposits`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: one blob has one decoding
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: craft a body with two valid decodings and assert one is chosen
