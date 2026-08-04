# Q3044: false-spent partial write in Fp.hashCode

## Question
Can an unprivileged attacker cause /wallet/validateaddress to make crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java::hashCode mark a note or spend object in the canonical byte representation or derived key/address before all later checks succeed, so the user’s value becomes unrecoverable in the intended owner, transaction context, or verification result and the final outcome is Canonicalization bugs that mark valid assets or notes unreachable or falsely spent?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java::hashCode
- Entrypoint: /wallet/validateaddress
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Target flows where note-spent markers, nullifiers, or anchor-linked records are written before final proof and accounting completion.
- Invariant to test: Shielded state must only advance to spent/final after every proof, amount, and accounting check has succeeded.
- Expected Immunefi impact: Canonicalization bugs that mark valid assets or notes unreachable or falsely spent
- Fast validation: Inject failures after each shielded-state write via /wallet/validateaddress; assert no valid note becomes falsely spent or unrecoverable.
