# Q1447: SM2Signer: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2Signer.generateSignature` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` — where the attacker submits a non-canonical (high-s) or over-length signature that SM2Signer.generateSignature accepts, enabling replay or weight double-count — to break the invariant that SM2Signer.generateSignature rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` -> `SM2Signer.generateSignature`
- Entrypoint: transaction/precompile path invoking SM2Signer.generateSignature
- Attacker controls: request/transaction/contract inputs to `SM2Signer.generateSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that SM2Signer.generateSignature accepts, enabling replay or weight double-count
- Invariant to test: SM2Signer.generateSignature rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
