# Q2800: SM2: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2.signatureToAddress` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` — where the attacker manipulates the recovery byte so SM2.signatureToAddress recovers an unintended address the attacker can predict — to break the invariant that SM2.signatureToAddress recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java` -> `SM2.signatureToAddress`
- Entrypoint: path calling SM2.signatureToAddress with crafted v
- Attacker controls: request/transaction/contract inputs to `SM2.signatureToAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so SM2.signatureToAddress recovers an unintended address the attacker can predict
- Invariant to test: SM2.signatureToAddress recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
