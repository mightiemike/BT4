# Q1635: commitment stored once semantics via `build_services` (services.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling which commitment indices are covered, drive `build_services` in `crates/light-client-prover/src/services.rs` so that the commitment stored for an index and the first commitment seen for that index stop being the same object, breaking the invariant that the first valid commitment per index wins, deterministically?

## Target
- File/function: `crates/light-client-prover/src/services.rs` -> `build_services`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: which commitment indices are covered
- Exploit idea: commitment stored once semantics - reach `build_services` from that entrypoint and force the divergence where the commitment stored for an index and the first commitment seen for that index stop being the same object; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the first valid commitment per index wins, deterministically
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe two commitments for one index and assert stable selection
