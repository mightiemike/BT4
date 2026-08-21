# Q1367: SM2Signer: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2Signer.generateHashSignature` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` — where the attacker collects signatures from SM2Signer.generateHashSignature to detect a reused or biased k allowing key recovery — to break the invariant that SM2Signer.generateHashSignature uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` -> `SM2Signer.generateHashSignature`
- Entrypoint: collect signatures produced via SM2Signer.generateHashSignature
- Attacker controls: request/transaction/contract inputs to `SM2Signer.generateHashSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from SM2Signer.generateHashSignature to detect a reused or biased k allowing key recovery
- Invariant to test: SM2Signer.generateHashSignature uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
