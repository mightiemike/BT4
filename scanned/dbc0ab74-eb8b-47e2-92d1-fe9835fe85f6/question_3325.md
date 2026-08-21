# Q3325: Hash: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Hash.sha3omit12` in `crypto/src/main/java/org/tron/common/crypto/Hash.java` — where the attacker collects signatures from Hash.sha3omit12 to detect a reused or biased k allowing key recovery — to break the invariant that Hash.sha3omit12 uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Hash.java` -> `Hash.sha3omit12`
- Entrypoint: collect signatures produced via Hash.sha3omit12
- Attacker controls: request/transaction/contract inputs to `Hash.sha3omit12` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Hash.sha3omit12 to detect a reused or biased k allowing key recovery
- Invariant to test: Hash.sha3omit12 uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
