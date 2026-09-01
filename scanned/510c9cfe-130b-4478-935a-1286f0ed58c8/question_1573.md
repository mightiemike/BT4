# Q1573: historical state served from wrong root via `get_transaction_receipt` (query.rs)

## Question
Can an unprivileged attacker who calls `eth_call` against an attacker-deployed contract at a historical block tag, controlling the storage slots its contract touches, drive `get_transaction_receipt` in `crates/evm/src/query.rs` so that the state root the query executes against and the root of the block tag requested stop being the same root, breaking the invariant that every query answer is anchored to the requested block's state root?

## Target
- File/function: `crates/evm/src/query.rs` -> `get_transaction_receipt`
- Entrypoint: unprivileged party calls `eth_call` against an attacker-deployed contract at a historical block tag
- Attacker controls: the storage slots its contract touches
- Exploit idea: historical state served from wrong root - reach `get_transaction_receipt` from that entrypoint and force the divergence where the state root the query executes against and the root of the block tag requested stop being the same root; the adjacent symbols in the same file that carry the value are `EstimatedTxExpenses`, `EstimatedDiffSize`, `gas_with_l1_overhead`, `l1_fee`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every query answer is anchored to the requested block's state root
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: query an attacker-touched slot at a historical tag and diff against archival replay
