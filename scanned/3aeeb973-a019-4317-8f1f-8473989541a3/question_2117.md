# Q2117: system contract callable by users via `set_block_info` (mod.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling contract bytecode and calldata, drive `set_block_info` in `crates/evm/src/evm/system_contracts/mod.rs` so that the caller the system contract accepts and the system caller the protocol intends stop being the same party, breaking the invariant that only the system signer can drive `set_block_info` / `deposit`?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `set_block_info`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: contract bytecode and calldata
- Exploit idea: system contract callable by users - reach `set_block_info` from that entrypoint and force the divergence where the caller the system contract accepts and the system caller the protocol intends stop being the same party; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only the system signer can drive `set_block_info` / `deposit`
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: call the light client and bridge setters from an EOA and assert rejection
