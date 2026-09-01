# Q3248: genesis/system account preload via `empty_code` (genesis.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling contract bytecode and calldata, drive `empty_code` in `crates/evm/src/genesis.rs` so that the balances genesis installs and the balances the first proved root contains stop being equal, breaking the invariant that genesis state is exactly what the circuit starts from?

## Target
- File/function: `crates/evm/src/genesis.rs` -> `empty_code`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: contract bytecode and calldata
- Exploit idea: genesis/system account preload - reach `empty_code` from that entrypoint and force the divergence where the balances genesis installs and the balances the first proved root contains stop being equal; the adjacent symbols in the same file that carry the value are `AccountData`, `AccountDataHelper`, `EvmConfig`, `balance`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: genesis state is exactly what the circuit starts from
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: diff the genesis root against the circuit's initial root
