# Q2692: SignUtils: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SignUtils.signatureToAddress` in `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` — where the attacker collects signatures from SignUtils.signatureToAddress to detect a reused or biased k allowing key recovery — to break the invariant that SignUtils.signatureToAddress uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` -> `SignUtils.signatureToAddress`
- Entrypoint: collect signatures produced via SignUtils.signatureToAddress
- Attacker controls: request/transaction/contract inputs to `SignUtils.signatureToAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from SignUtils.signatureToAddress to detect a reused or biased k allowing key recovery
- Invariant to test: SignUtils.signatureToAddress uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
