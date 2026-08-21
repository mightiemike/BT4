# Q938: SM2Signer: key/secret in output or log

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2Signer.verifySignature` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` — where the attacker triggers SM2Signer.verifySignature error/exception path that serializes private/spending key material into a response or log — to break the invariant that SM2Signer.verifySignature never emits secret material to any sink, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` -> `SM2Signer.verifySignature`
- Entrypoint: force an error in SM2Signer.verifySignature
- Attacker controls: request/transaction/contract inputs to `SM2Signer.verifySignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers SM2Signer.verifySignature error/exception path that serializes private/spending key material into a response or log
- Invariant to test: SM2Signer.verifySignature never emits secret material to any sink
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: JUnit asserting no key bytes in exception/log output
