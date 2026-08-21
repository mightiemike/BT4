# Q372: BN128: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `BN128.instance` in `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` — where the attacker manipulates the recovery byte so BN128.instance recovers an unintended address the attacker can predict — to break the invariant that BN128.instance recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java` -> `BN128.instance`
- Entrypoint: path calling BN128.instance with crafted v
- Attacker controls: request/transaction/contract inputs to `BN128.instance` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so BN128.instance recovers an unintended address the attacker can predict
- Invariant to test: BN128.instance recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
