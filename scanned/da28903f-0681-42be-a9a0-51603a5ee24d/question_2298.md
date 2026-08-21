# Q2298: Hash: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.ripemd160` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker manipulates the recovery byte so Hash.ripemd160 recovers an unintended address the attacker can predict — to break the invariant that Hash.ripemd160 recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.ripemd160`
- Entrypoint: path calling Hash.ripemd160 with crafted v
- Attacker controls: request/transaction/contract inputs to `Hash.ripemd160` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so Hash.ripemd160 recovers an unintended address the attacker can predict
- Invariant to test: Hash.ripemd160 recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
