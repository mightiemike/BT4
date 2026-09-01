# Q4007: blockhash opcode over the L1 contract via `empty_code` (genesis.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling value and gas, drive `empty_code` in `crates/evm/src/genesis.rs` so that the hash the `blockhash` opcode returns and the hash the light client contract holds for that height stop being the same, breaking the invariant that contract-visible history equals verified history?

## Target
- File/function: `crates/evm/src/genesis.rs` -> `empty_code`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: value and gas
- Exploit idea: blockhash opcode over the L1 contract - reach `empty_code` from that entrypoint and force the divergence where the hash the `blockhash` opcode returns and the hash the light client contract holds for that height stop being the same; the adjacent symbols in the same file that carry the value are `AccountData`, `AccountDataHelper`, `EvmConfig`, `balance`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: contract-visible history equals verified history
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: compare opcode output with the contract for the same height
