# Q3988: offchain versus onchain key separation via `with_codec` (value.rs)

## Question
Can an unprivileged attacker who drives a stored value across an encoding boundary, controlling the key under which it is stored, drive `with_codec` in `crates/sovereign-sdk/module-system/sov-modules-api/src/containers/value.rs` so that the keys that affect the state root and the keys declared offchain stop being disjoint, breaking the invariant that offchain writes never move the root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-api/src/containers/value.rs` -> `with_codec`
- Entrypoint: unprivileged party drives a stored value across an encoding boundary
- Attacker controls: the key under which it is stored
- Exploit idea: offchain versus onchain key separation - reach `with_codec` from that entrypoint and force the divergence where the keys that affect the state root and the keys declared offchain stop being disjoint; the adjacent symbols in the same file that carry the value are `StateValue`, `prefix`, `codec`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: offchain writes never move the root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: write offchain state and assert an unchanged root
