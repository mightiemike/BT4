# Q3392: SM2Signer: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2Signer.generateSignature` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` — where the attacker submits a signature/pubkey to SM2Signer.generateSignature that is short or padded but still parsed, recovering a shifted value — to break the invariant that SM2Signer.generateSignature enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` -> `SM2Signer.generateSignature`
- Entrypoint: precompile/verify path to SM2Signer.generateSignature
- Attacker controls: request/transaction/contract inputs to `SM2Signer.generateSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to SM2Signer.generateSignature that is short or padded but still parsed, recovering a shifted value
- Invariant to test: SM2Signer.generateSignature enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
