# Q2561: conversion between revm and sov types via `is_first_time_committing_address` (db.rs)

## Question
Can an unprivileged attacker who deploys at a salt it previously destroyed, controlling the CREATE2 salt and init code, drive `is_first_time_committing_address` in `crates/evm/src/evm/db.rs` so that the value on the revm side and the value on the sov side after conversion stop being the same, breaking the invariant that type conversions are lossless?

## Target
- File/function: `crates/evm/src/evm/db.rs` -> `is_first_time_committing_address`
- Entrypoint: unprivileged party deploys at a salt it previously destroyed
- Attacker controls: the CREATE2 salt and init code
- Exploit idea: conversion between revm and sov types - reach `is_first_time_committing_address` from that entrypoint and force the divergence where the value on the revm side and the value on the sov side after conversion stop being the same; the adjacent symbols in the same file that carry the value are `DBError`, `EvmDb`, `AccountExistsProvider`, `EvmDbRef`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: type conversions are lossless
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fuzz round-trips for U256/address/log types
