# Q1738: deposit body decoded twice differently via `fetch_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height, controlling the entire `Bytes` deposit payload, drive `fetch_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the deposit body the simulation decodes and the body the block execution decodes stop being the same structure, breaking the invariant that one blob has one decoding?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `fetch_deposits`
- Entrypoint: unprivileged party submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: deposit body decoded twice differently - reach `fetch_deposits` from that entrypoint and force the divergence where the deposit body the simulation decodes and the body the block execution decodes stop being the same structure; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `remove_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: one blob has one decoding
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: craft a body with two valid decodings and assert one is chosen
