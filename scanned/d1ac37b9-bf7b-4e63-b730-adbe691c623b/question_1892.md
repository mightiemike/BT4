# Q1892: ECKey: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.signatureToKeyBytes` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker manipulates the recovery byte so ECKey.signatureToKeyBytes recovers an unintended address the attacker can predict — to break the invariant that ECKey.signatureToKeyBytes recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.signatureToKeyBytes`
- Entrypoint: path calling ECKey.signatureToKeyBytes with crafted v
- Attacker controls: request/transaction/contract inputs to `ECKey.signatureToKeyBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so ECKey.signatureToKeyBytes recovers an unintended address the attacker can predict
- Invariant to test: ECKey.signatureToKeyBytes recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
