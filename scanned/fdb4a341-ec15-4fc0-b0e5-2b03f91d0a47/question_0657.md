# Q0657: precompile gas versus work via `verify_sig` (schnorr.rs)

## Question
Can an unprivileged attacker who calls the precompile from a contract at the exact gas boundary, controlling precompile input length and bytes, drive `verify_sig` in `crates/evm/src/evm/precompiles/schnorr.rs` so that the gas `SCHNORRVERIFY_BASE` charges and the work the precompile performs stop being proportional, breaking the invariant that no precompile lets an attacker buy unbounded verification?

## Target
- File/function: `crates/evm/src/evm/precompiles/schnorr.rs` -> `verify_sig`
- Entrypoint: unprivileged party calls the precompile from a contract at the exact gas boundary
- Attacker controls: precompile input length and bytes
- Exploit idea: precompile gas versus work - reach `verify_sig` from that entrypoint and force the divergence where the gas `SCHNORRVERIFY_BASE` charges and the work the precompile performs stop being proportional; the adjacent symbols in the same file that carry the value are `schnorr_verify`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no precompile lets an attacker buy unbounded verification
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: measure worst-case cost against the flat charge
