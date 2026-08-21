# Q2139: SignUtils: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SignUtils.getGeneratedRandomSign` in `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` — where the attacker collects signatures from SignUtils.getGeneratedRandomSign to detect a reused or biased k allowing key recovery — to break the invariant that SignUtils.getGeneratedRandomSign uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` -> `SignUtils.getGeneratedRandomSign`
- Entrypoint: collect signatures produced via SignUtils.getGeneratedRandomSign
- Attacker controls: request/transaction/contract inputs to `SignUtils.getGeneratedRandomSign` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from SignUtils.getGeneratedRandomSign to detect a reused or biased k allowing key recovery
- Invariant to test: SignUtils.getGeneratedRandomSign uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
