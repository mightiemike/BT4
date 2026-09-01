# Q3100: utxo selection reuse via `try_from` (address.rs)

## Question
Can an unprivileged attacker who RBFs an inscription so two candidate reveals exist for the same logical blob, controlling block position and RBF replacement, drive `try_from` in `crates/bitcoin-da/src/spec/address.rs` so that the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same, breaking the invariant that the node never double-spends its own DA funding?

## Target
- File/function: `crates/bitcoin-da/src/spec/address.rs` -> `try_from`
- Entrypoint: unprivileged party RBFs an inscription so two candidate reveals exist for the same logical blob
- Attacker controls: block position and RBF replacement
- Exploit idea: utxo selection reuse - reach `try_from` from that entrypoint and force the divergence where the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same; the adjacent symbols in the same file that carry the value are `AddressWrapper`, `from_str`, `as_ref`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the node never double-spends its own DA funding
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: race two publishes on one UTXO and assert serialisation
