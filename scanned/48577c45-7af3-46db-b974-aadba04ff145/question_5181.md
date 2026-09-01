# Q5181: blob body versus committed body via `create_inscription_type_1` (body_builders.rs)

## Question
Can an unprivileged attacker who inscribes a reveal whose tapscript envelope splits the body across push boundaries, controlling the envelope/tapscript layout, drive `create_inscription_type_1` in `crates/bitcoin-da/src/helpers/builders/body_builders.rs` so that the blob body handed to the STF and the body committed by the reveal's witness stop being the same bytes, breaking the invariant that blob contents are committed by the Bitcoin transaction?

## Target
- File/function: `crates/bitcoin-da/src/helpers/builders/body_builders.rs` -> `create_inscription_type_1`
- Entrypoint: unprivileged party inscribes a reveal whose tapscript envelope splits the body across push boundaries
- Attacker controls: the envelope/tapscript layout
- Exploit idea: blob body versus committed body - reach `create_inscription_type_1` from that entrypoint and force the divergence where the blob body handed to the STF and the body committed by the reveal's witness stop being the same bytes; the adjacent symbols in the same file that carry the value are `RawTxData`, `DaTxs`, `mine_reveal_prefix`, `verify_commit_address`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: blob contents are committed by the Bitcoin transaction
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: mutate the body post-parse and assert the commitment check fails
