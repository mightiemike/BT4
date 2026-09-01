# Q1185: journal hash preimage binding via `lib` (lib.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling which commitment indices are covered, drive `lib` in `crates/light-client-prover/src/lib.rs` so that the fields hashed into the committed journal and the fields the verifier re-derives stop being the same tuple, breaking the invariant that journal commitment covers every field a consumer trusts?

## Target
- File/function: `crates/light-client-prover/src/lib.rs` -> `lib`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: which commitment indices are covered
- Exploit idea: journal hash preimage binding - reach `lib` from that entrypoint and force the divergence where the fields hashed into the committed journal and the fields the verifier re-derives stop being the same tuple; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: journal commitment covers every field a consumer trusts
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: mutate an uncommitted field and assert the journal changes
