# Q1294: Rsv: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Rsv.fromSignature` in `crypto/src/main/java/org/tron/common/crypto/Rsv.java` — where the attacker manipulates the recovery byte so Rsv.fromSignature recovers an unintended address the attacker can predict — to break the invariant that Rsv.fromSignature recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Rsv.java` -> `Rsv.fromSignature`
- Entrypoint: path calling Rsv.fromSignature with crafted v
- Attacker controls: request/transaction/contract inputs to `Rsv.fromSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so Rsv.fromSignature recovers an unintended address the attacker can predict
- Invariant to test: Rsv.fromSignature recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
