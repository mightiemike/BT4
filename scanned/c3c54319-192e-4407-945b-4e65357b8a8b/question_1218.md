# Q1218: deposit blob admitted but not executable via `add_deposit_tx` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob, controlling the entire `Bytes` deposit payload, drive `add_deposit_tx` in `crates/sequencer/src/deposit_data_mempool.rs` so that the deposit that `eth_call` simulated at admission time and the deposit the Bridge system contract actually executes at inclusion height stop being the same call, breaking the invariant that a deposit accepted into `DepositDataMempool` executes with identical semantics at inclusion?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `add_deposit_tx`
- Entrypoint: unprivileged party calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: deposit blob admitted but not executable - reach `add_deposit_tx` from that entrypoint and force the divergence where the deposit that `eth_call` simulated at admission time and the deposit the Bridge system contract actually executes at inclusion height stop being the same call; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `remove_deposits`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a deposit accepted into `DepositDataMempool` executes with identical semantics at inclusion
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: unit-test the mempool admission path, then apply the same blob one block later and diff the Bridge call outcome
