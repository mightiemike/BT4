# Q3523: conversion between revm and sov types via `encode` (primitive_types.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key, controlling the CREATE2 salt and init code, drive `encode` in `crates/evm/src/evm/primitive_types.rs` so that the value on the revm side and the value on the sov side after conversion stop being the same, breaking the invariant that type conversions are lossless?

## Target
- File/function: `crates/evm/src/evm/primitive_types.rs` -> `encode`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key
- Attacker controls: the CREATE2 salt and init code
- Exploit idea: conversion between revm and sov types - reach `encode` from that entrypoint and force the divergence where the value on the revm side and the value on the sov side after conversion stop being the same; the adjacent symbols in the same file that carry the value are `RlpEvmTransaction`, `TransactionSignedAndRecovered`, `Block`, `SealedBlock`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: type conversions are lossless
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fuzz round-trips for U256/address/log types
