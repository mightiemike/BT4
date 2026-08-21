# Q865: Blake2bfMessageDigest: weak nonce / RNG reuse

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Blake2bfMessageDigest.update` in `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` — where the attacker collects signatures from Blake2bfMessageDigest.update to detect a reused or biased k allowing key recovery — to break the invariant that Blake2bfMessageDigest.update uses a unique unbiased nonce per signature, leading to: Private-key disclosure (Fatal)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/Blake2bfMessageDigest.java` -> `Blake2bfMessageDigest.update`
- Entrypoint: collect signatures produced via Blake2bfMessageDigest.update
- Attacker controls: request/transaction/contract inputs to `Blake2bfMessageDigest.update` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: collects signatures from Blake2bfMessageDigest.update to detect a reused or biased k allowing key recovery
- Invariant to test: Blake2bfMessageDigest.update uses a unique unbiased nonce per signature
- Expected Immunefi impact: Private-key disclosure (Fatal)
- Fast validation: statistical test for nonce reuse across signatures
