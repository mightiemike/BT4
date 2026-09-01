# Q3997: system event ordering versus user txs via `empty_code` (genesis.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling value and gas, drive `empty_code` in `crates/evm/src/genesis.rs` so that the position system events occupy in the block and the position the STF assumes stop being the same, breaking the invariant that system events are first-class and fixed in order?

## Target
- File/function: `crates/evm/src/genesis.rs` -> `empty_code`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: value and gas
- Exploit idea: system event ordering versus user txs - reach `empty_code` from that entrypoint and force the divergence where the position system events occupy in the block and the position the STF assumes stop being the same; the adjacent symbols in the same file that carry the value are `AccountData`, `AccountDataHelper`, `EvmConfig`, `balance`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system events are first-class and fixed in order
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: interleave and re-execute the block
