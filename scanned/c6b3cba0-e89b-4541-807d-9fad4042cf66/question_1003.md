# Q1003: SM2Signer: recovery id / v confusion

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2Signer.generateHashSignature` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` — where the attacker manipulates the recovery byte so SM2Signer.generateHashSignature recovers an unintended address the attacker can predict — to break the invariant that SM2Signer.generateHashSignature recovers exactly one address for a valid signature, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` -> `SM2Signer.generateHashSignature`
- Entrypoint: path calling SM2Signer.generateHashSignature with crafted v
- Attacker controls: request/transaction/contract inputs to `SM2Signer.generateHashSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: manipulates the recovery byte so SM2Signer.generateHashSignature recovers an unintended address the attacker can predict
- Invariant to test: SM2Signer.generateHashSignature recovers exactly one address for a valid signature
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit varying v and asserting single valid recovery
