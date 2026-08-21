# Q1835: ECKey: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `ECKey.recoverFromSignature` in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` — where the attacker manipulates the recovery byte so ECKey.recoverFromSignature recovers an unintended address the attacker can predict — to break the invariant that ECKey.recoverFromSignature recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/ECKey.java` -> `ECKey.recoverFromSignature`
- Entrypoint: path calling ECKey.recoverFromSignature with crafted v
- Attacker controls: request/transaction/contract inputs to `ECKey.recoverFromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so ECKey.recoverFromSignature recovers an unintended address the attacker can predict
- Invariant to test: ECKey.recoverFromSignature recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
