# Q5512: wtxid prefix collision with an ordinary tx via `create_inscription_type_0` (body_builders.rs)

## Question
Can an unprivileged attacker who pays Bitcoin fees to inscribe a reveal transaction whose wtxid matches `reveal_tx_prefix`, controlling the wtxid via nonce grinding, drive `create_inscription_type_0` in `crates/bitcoin-da/src/helpers/builders/body_builders.rs` so that the transactions the prefix filter treats as protocol data and the transactions actually authored by protocol roles stop being the same set, breaking the invariant that prefix matching is a hint, never an authorisation?

## Target
- File/function: `crates/bitcoin-da/src/helpers/builders/body_builders.rs` -> `create_inscription_type_0`
- Entrypoint: unprivileged party pays Bitcoin fees to inscribe a reveal transaction whose wtxid matches `reveal_tx_prefix`
- Attacker controls: the wtxid via nonce grinding
- Exploit idea: wtxid prefix collision with an ordinary tx - reach `create_inscription_type_0` from that entrypoint and force the divergence where the transactions the prefix filter treats as protocol data and the transactions actually authored by protocol roles stop being the same set; the adjacent symbols in the same file that carry the value are `RawTxData`, `DaTxs`, `mine_reveal_prefix`, `verify_commit_address`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: prefix matching is a hint, never an authorisation
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: grind an ordinary transaction into the prefix and assert it is dropped on authentication, not on shape
