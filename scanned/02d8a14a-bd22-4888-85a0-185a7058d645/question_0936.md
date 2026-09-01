# Q0936: sender attribution from the reveal via `sender` (blob.rs)

## Question
Can an unprivileged attacker who pays Bitcoin fees to inscribe a reveal transaction whose wtxid matches `reveal_tx_prefix`, controlling the body encoding, drive `sender` in `crates/bitcoin-da/src/spec/blob.rs` so that the public key `blob.sender()` reports and the key that actually signed the reveal stop being the same key, breaking the invariant that sender attribution is cryptographically bound?

## Target
- File/function: `crates/bitcoin-da/src/spec/blob.rs` -> `sender`
- Entrypoint: unprivileged party pays Bitcoin fees to inscribe a reveal transaction whose wtxid matches `reveal_tx_prefix`
- Attacker controls: the body encoding
- Exploit idea: sender attribution from the reveal - reach `sender` from that entrypoint and force the divergence where the public key `blob.sender()` reports and the key that actually signed the reveal stop being the same key; the adjacent symbols in the same file that carry the value are `BlobWithSender`, `wtxid`, `full_data`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: sender attribution is cryptographically bound
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe with a spoofed-looking script and assert attribution fails
