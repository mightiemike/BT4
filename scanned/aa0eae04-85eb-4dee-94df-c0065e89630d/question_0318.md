# Q318: ECKey: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.signatureToKey` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker manipulates the recovery byte so ECKey.signatureToKey recovers an unintended address the attacker can predict — to break the invariant that ECKey.signatureToKey recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.signatureToKey`
- Entrypoint: path calling ECKey.signatureToKey with crafted v
- Attacker controls: request/transaction/contract inputs to `ECKey.signatureToKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so ECKey.signatureToKey recovers an unintended address the attacker can predict
- Invariant to test: ECKey.signatureToKey recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
