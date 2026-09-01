# Q5955: txid versus wtxid confusion via `bits_to_target` (verifier.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling the block's transaction set and coinbase, drive `bits_to_target` in `crates/bitcoin-da/src/verifier.rs` so that the identifier used to index a blob and the identifier the merkle proof commits stop being the same, breaking the invariant that blob identity is unambiguous?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `bits_to_target`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: txid versus wtxid confusion - reach `bits_to_target` from that entrypoint and force the divergence where the identifier used to index a blob and the identifier the merkle proof commits stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: blob identity is unambiguous
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: craft a transaction where txid and wtxid paths diverge
