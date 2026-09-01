# Q3108: blockhash opcode over the L1 contract via `owner` (mod.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling contract bytecode and calldata, drive `owner` in `crates/evm/src/evm/system_contracts/mod.rs` so that the hash the `blockhash` opcode returns and the hash the light client contract holds for that height stop being the same, breaking the invariant that contract-visible history equals verified history?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `owner`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: contract bytecode and calldata
- Exploit idea: blockhash opcode over the L1 contract - reach `owner` from that entrypoint and force the divergence where the hash the `blockhash` opcode returns and the hash the light client contract holds for that height stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: contract-visible history equals verified history
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: compare opcode output with the contract for the same height
