# Q3345: sender attribution from the reveal via `mine_reveal_prefix` (body_builders.rs)

## Question
Can an unprivileged attacker who inscribes a reveal whose parsed body deserialises differently under two code paths, controlling the body encoding, drive `mine_reveal_prefix` in `crates/bitcoin-da/src/helpers/builders/body_builders.rs` so that the public key `blob.sender()` reports and the key that actually signed the reveal stop being the same key, breaking the invariant that sender attribution is cryptographically bound?

## Target
- File/function: `crates/bitcoin-da/src/helpers/builders/body_builders.rs` -> `mine_reveal_prefix`
- Entrypoint: unprivileged party inscribes a reveal whose parsed body deserialises differently under two code paths
- Attacker controls: the body encoding
- Exploit idea: sender attribution from the reveal - reach `mine_reveal_prefix` from that entrypoint and force the divergence where the public key `blob.sender()` reports and the key that actually signed the reveal stop being the same key; the adjacent symbols in the same file that carry the value are `RawTxData`, `DaTxs`, `verify_commit_address`, `create_inscription_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: sender attribution is cryptographically bound
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe with a spoofed-looking script and assert attribution fails
