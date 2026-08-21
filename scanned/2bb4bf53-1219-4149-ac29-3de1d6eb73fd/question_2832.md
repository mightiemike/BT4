# Q2832: SignUtils: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SignUtils.signatureToAddress` in `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` — where the attacker manipulates the recovery byte so SignUtils.signatureToAddress recovers an unintended address the attacker can predict — to break the invariant that SignUtils.signatureToAddress recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` -> `SignUtils.signatureToAddress`
- Entrypoint: path calling SignUtils.signatureToAddress with crafted v
- Attacker controls: request/transaction/contract inputs to `SignUtils.signatureToAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so SignUtils.signatureToAddress recovers an unintended address the attacker can predict
- Invariant to test: SignUtils.signatureToAddress recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
