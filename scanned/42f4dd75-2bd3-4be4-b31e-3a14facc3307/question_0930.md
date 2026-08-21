# Q930: SM2Signer: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2Signer.verifyHashSignature` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` — where the attacker submits a non-canonical (high-s) or over-length signature that SM2Signer.verifyHashSignature accepts, enabling replay or weight double-count — to break the invariant that SM2Signer.verifyHashSignature rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` -> `SM2Signer.verifyHashSignature`
- Entrypoint: transaction/precompile path invoking SM2Signer.verifyHashSignature
- Attacker controls: request/transaction/contract inputs to `SM2Signer.verifyHashSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that SM2Signer.verifyHashSignature accepts, enabling replay or weight double-count
- Invariant to test: SM2Signer.verifyHashSignature rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
