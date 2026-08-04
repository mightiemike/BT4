# Q3047: value-fee boundary bug in Fp.hashCode

## Question
Can an unprivileged attacker send boundary note values, fees, or mixed transparent/shielded amounts through /wallet/scanshieldedtrc20notesbyovk so crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java::hashCode misprices or misbalances the flow and reaches Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/zksnark/Fp.java::hashCode
- Entrypoint: /wallet/scanshieldedtrc20notesbyovk
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Stress min/max note values, zero-like notes, fee boundaries, and mixed transparent/shielded amount distributions.
- Invariant to test: Shielded value, transparent value, and fees must conserve exactly across every accepted boundary case.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Boundary-fuzz all numeric shielded fields through /wallet/scanshieldedtrc20notesbyovk; assert exact conservation and expected rejections.
