# Q2970: Hash: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.encodeElement` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker manipulates the recovery byte so Hash.encodeElement recovers an unintended address the attacker can predict — to break the invariant that Hash.encodeElement recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.encodeElement`
- Entrypoint: path calling Hash.encodeElement with crafted v
- Attacker controls: request/transaction/contract inputs to `Hash.encodeElement` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so Hash.encodeElement recovers an unintended address the attacker can predict
- Invariant to test: Hash.encodeElement recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
