# Q2842: partial-validation leak in Keccak256.copy

## Question
Can an unprivileged attacker make /wallet/gettriggerinputforshieldedtrc20contract trigger crypto/src/main/java/org/tron/common/crypto/cryptohash/Keccak256.java::copy so some security-relevant state advances before full validation finishes, leaving a digest, key, or object in a half-accepted state and causing Canonicalization bugs that mark valid assets or notes unreachable or falsely spent?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/cryptohash/Keccak256.java::copy
- Entrypoint: /wallet/gettriggerinputforshieldedtrc20contract
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Search for caches, marks, or derived objects that are persisted before the final reject/accept decision.
- Invariant to test: No security-relevant state should advance on attacker-controlled input until the full validation path has succeeded.
- Expected Immunefi impact: Canonicalization bugs that mark valid assets or notes unreachable or falsely spent
- Fast validation: Inject failures after each intermediate validation step reachable from /wallet/gettriggerinputforshieldedtrc20contract; assert no partial state survives a rejected input.
