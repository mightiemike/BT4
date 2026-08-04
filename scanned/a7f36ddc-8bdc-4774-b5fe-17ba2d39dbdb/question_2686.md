# Q2686: partial-validation leak in Sha256Hash.create

## Question
Can an unprivileged attacker make /wallet/broadcasttransaction trigger common/src/main/java/org/tron/common/utils/Sha256Hash.java::create so some security-relevant state advances before full validation finishes, leaving a digest, key, or object in a half-accepted state and causing Canonicalization bugs that mark valid assets or notes unreachable or falsely spent?

## Target
- File/function: common/src/main/java/org/tron/common/utils/Sha256Hash.java::create
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Search for caches, marks, or derived objects that are persisted before the final reject/accept decision.
- Invariant to test: No security-relevant state should advance on attacker-controlled input until the full validation path has succeeded.
- Expected Immunefi impact: Canonicalization bugs that mark valid assets or notes unreachable or falsely spent
- Fast validation: Inject failures after each intermediate validation step reachable from /wallet/broadcasttransaction; assert no partial state survives a rejected input.
