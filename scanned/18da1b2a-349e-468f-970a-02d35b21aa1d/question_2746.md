# Q2746: partial-validation leak in ECKey.check

## Question
Can an unprivileged attacker make /jsonrpc eth_sendRawTransaction trigger crypto/src/main/java/org/tron/common/crypto/ECKey.java::check so some security-relevant state advances before full validation finishes, leaving a digest, key, or object in a half-accepted state and causing Canonicalization bugs that mark valid assets or notes unreachable or falsely spent?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/ECKey.java::check
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Search for caches, marks, or derived objects that are persisted before the final reject/accept decision.
- Invariant to test: No security-relevant state should advance on attacker-controlled input until the full validation path has succeeded.
- Expected Immunefi impact: Canonicalization bugs that mark valid assets or notes unreachable or falsely spent
- Fast validation: Inject failures after each intermediate validation step reachable from /jsonrpc eth_sendRawTransaction; assert no partial state survives a rejected input.
