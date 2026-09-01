# Q0069: precompile gas versus work via `mod` (mod.rs)

## Question
Can an unprivileged attacker who invokes the schnorr precompile with attacker-chosen input length and bytes, controlling precompile input length and bytes, drive `mod` in `crates/evm/src/evm/precompiles/mod.rs` so that the gas `SCHNORRVERIFY_BASE` charges and the work the precompile performs stop being proportional, breaking the invariant that no precompile lets an attacker buy unbounded verification?

## Target
- File/function: `crates/evm/src/evm/precompiles/mod.rs` -> `mod`
- Entrypoint: unprivileged party invokes the schnorr precompile with attacker-chosen input length and bytes
- Attacker controls: precompile input length and bytes
- Exploit idea: precompile gas versus work - reach `mod` from that entrypoint and force the divergence where the gas `SCHNORRVERIFY_BASE` charges and the work the precompile performs stop being proportional; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no precompile lets an attacker buy unbounded verification
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: measure worst-case cost against the flat charge
