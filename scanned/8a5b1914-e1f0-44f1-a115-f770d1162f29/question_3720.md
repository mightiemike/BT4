# Q3720: filter surviving a prune boundary via `max_fee_per_gas` (ethereum.rs)

## Question
Can an unprivileged attacker who emits logs from an attacker-deployed contract and then queries them back, controlling poll timing across blocks, drive `max_fee_per_gas` in `crates/ethereum-rpc/src/ethereum.rs` so that the range a filter still answers for and the range the node can still prove stop being the same, breaking the invariant that answers are bounded by provable history?

## Target
- File/function: `crates/ethereum-rpc/src/ethereum.rs` -> `max_fee_per_gas`
- Entrypoint: unprivileged party emits logs from an attacker-deployed contract and then queries them back
- Attacker controls: poll timing across blocks
- Exploit idea: filter surviving a prune boundary - reach `max_fee_per_gas` from that entrypoint and force the divergence where the range a filter still answers for and the range the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `EthRpcConfig`, `Ethereum`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: answers are bounded by provable history
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: poll a filter across the prune horizon and assert an explicit error
