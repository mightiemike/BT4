# Q4547: light client contract write path via `get_system_caller` (mod.rs)

## Question
Can an unprivileged attacker who sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space, controlling contract bytecode and calldata, drive `get_system_caller` in `crates/evm/src/evm/system_contracts/mod.rs` so that the L1 hash written by `set_block_info` and the L1 hash the DA layer actually produced stop being the same hash, breaking the invariant that the light client contract mirrors real Bitcoin headers?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `get_system_caller`
- Entrypoint: unprivileged party sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space
- Attacker controls: contract bytecode and calldata
- Exploit idea: light client contract write path - reach `get_system_caller` from that entrypoint and force the divergence where the L1 hash written by `set_block_info` and the L1 hash the DA layer actually produced stop being the same hash; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the light client contract mirrors real Bitcoin headers
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed a crafted header and assert the contract rejects it
