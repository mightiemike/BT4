# Q4882: witness root exposure via `deposit` (mod.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling the target system-contract address and selector, drive `deposit` in `crates/evm/src/evm/system_contracts/mod.rs` so that the witness root a contract reads and the witness root of the referenced L1 block stop being equal, breaking the invariant that contract-visible L1 data equals verified L1 data?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `deposit`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: the target system-contract address and selector
- Exploit idea: witness root exposure - reach `deposit` from that entrypoint and force the divergence where the witness root a contract reads and the witness root of the referenced L1 block stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: contract-visible L1 data equals verified L1 data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: query `get_witness_root_by_number` for an unset height and assert a defined failure
