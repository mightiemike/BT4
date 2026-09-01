# Q2223: l1 fee field in RPC output via `eth_get_block_transaction_count_by_hash` (query.rs)

## Question
Can an unprivileged attacker who calls `eth_call` against an attacker-deployed contract at a historical block tag, controlling the storage slots its contract touches, drive `eth_get_block_transaction_count_by_hash` in `crates/evm/src/query.rs` so that the L1 fee reported in a transaction receipt and the L1 fee charged during execution stop being equal, breaking the invariant that reported fees equal charged fees?

## Target
- File/function: `crates/evm/src/query.rs` -> `eth_get_block_transaction_count_by_hash`
- Entrypoint: unprivileged party calls `eth_call` against an attacker-deployed contract at a historical block tag
- Attacker controls: the storage slots its contract touches
- Exploit idea: l1 fee field in RPC output - reach `eth_get_block_transaction_count_by_hash` from that entrypoint and force the divergence where the L1 fee reported in a transaction receipt and the L1 fee charged during execution stop being equal; the adjacent symbols in the same file that carry the value are `EstimatedTxExpenses`, `EstimatedDiffSize`, `gas_with_l1_overhead`, `l1_fee`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: reported fees equal charged fees
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: diff receipt fields against `TxInfo` for adversarial calldata
