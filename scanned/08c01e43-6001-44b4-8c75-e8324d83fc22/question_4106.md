# Q4106: utxo selection reuse via `as_ref` (address.rs)

## Question
Can an unprivileged attacker who broadcasts a conflicting spend of a UTXO the node intends to use, controlling block position and RBF replacement, drive `as_ref` in `crates/bitcoin-da/src/spec/address.rs` so that the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same, breaking the invariant that the node never double-spends its own DA funding?

## Target
- File/function: `crates/bitcoin-da/src/spec/address.rs` -> `as_ref`
- Entrypoint: unprivileged party broadcasts a conflicting spend of a UTXO the node intends to use
- Attacker controls: block position and RBF replacement
- Exploit idea: utxo selection reuse - reach `as_ref` from that entrypoint and force the divergence where the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same; the adjacent symbols in the same file that carry the value are `AddressWrapper`, `from_str`, `try_from`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the node never double-spends its own DA funding
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: race two publishes on one UTXO and assert serialisation
