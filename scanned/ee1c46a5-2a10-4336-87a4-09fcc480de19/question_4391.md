# Q4391: wtxid prefix collision with an ordinary tx via `full_data` (blob.rs)

## Question
Can an unprivileged attacker who mines or relays a Bitcoin transaction with a malformed tapscript envelope, controlling the full serialized Bitcoin transaction and witness, drive `full_data` in `crates/bitcoin-da/src/spec/blob.rs` so that the transactions the prefix filter treats as protocol data and the transactions actually authored by protocol roles stop being the same set, breaking the invariant that prefix matching is a hint, never an authorisation?

## Target
- File/function: `crates/bitcoin-da/src/spec/blob.rs` -> `full_data`
- Entrypoint: unprivileged party mines or relays a Bitcoin transaction with a malformed tapscript envelope
- Attacker controls: the full serialized Bitcoin transaction and witness
- Exploit idea: wtxid prefix collision with an ordinary tx - reach `full_data` from that entrypoint and force the divergence where the transactions the prefix filter treats as protocol data and the transactions actually authored by protocol roles stop being the same set; the adjacent symbols in the same file that carry the value are `BlobWithSender`, `sender`, `wtxid`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: prefix matching is a hint, never an authorisation
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: grind an ordinary transaction into the prefix and assert it is dropped on authentication, not on shape
