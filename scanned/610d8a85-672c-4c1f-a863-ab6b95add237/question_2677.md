# Q2677: encoding-alias confusion in Sha256Hash.toString

## Question
Can an unprivileged attacker supply alternate public encodings through /wallet/broadcasttransaction so common/src/main/java/org/tron/common/utils/Sha256Hash.java::toString resolves one user-visible address, key, or identifier to a different internal object and the mismatch can be chained into Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: common/src/main/java/org/tron/common/utils/Sha256Hash.java::toString
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Probe base58, bech32, hex, visible-flag, prefix, padding, and truncation variants that all pass parsing.
- Invariant to test: Each accepted external encoding must map to one exact internal object and every caller-visible representation must agree on that mapping.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Fuzz all accepted encodings for the same logical object through /wallet/broadcasttransaction; assert they always resolve to one identical byte representation and one identical object.
