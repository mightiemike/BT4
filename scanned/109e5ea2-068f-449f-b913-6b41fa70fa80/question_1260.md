# Q1260: Credentials: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Credentials.getSignInterface` in `crypto/src/main/java/org/tron/keystore/Credentials.java` — where the attacker collects signatures from Credentials.getSignInterface to detect a reused or biased k allowing key recovery — to break the invariant that Credentials.getSignInterface uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Credentials.java` -> `Credentials.getSignInterface`
- Entrypoint: collect signatures produced via Credentials.getSignInterface
- Attacker controls: request/transaction/contract inputs to `Credentials.getSignInterface` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Credentials.getSignInterface to detect a reused or biased k allowing key recovery
- Invariant to test: Credentials.getSignInterface uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
