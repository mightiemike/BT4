# Q2785: encoding-alias confusion in SignUtils.signatureToAddress

## Question
Can an unprivileged attacker supply alternate public encodings through /wallet/broadcasthex so crypto/src/main/java/org/tron/common/crypto/SignUtils.java::signatureToAddress resolves one user-visible address, key, or identifier to a different internal object and the mismatch can be chained into Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/SignUtils.java::signatureToAddress
- Entrypoint: /wallet/broadcasthex
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Probe base58, bech32, hex, visible-flag, prefix, padding, and truncation variants that all pass parsing.
- Invariant to test: Each accepted external encoding must map to one exact internal object and every caller-visible representation must agree on that mapping.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Fuzz all accepted encodings for the same logical object through /wallet/broadcasthex; assert they always resolve to one identical byte representation and one identical object.
