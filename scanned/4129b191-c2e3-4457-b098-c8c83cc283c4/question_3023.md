# Q3023: value-fee boundary bug in BN128G2.create

## Question
Can an unprivileged attacker send boundary note values, fees, or mixed transparent/shielded amounts through /wallet/scanshieldedtrc20notesbyivk so crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java::create misprices or misbalances the flow and reaches Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java::create
- Entrypoint: /wallet/scanshieldedtrc20notesbyivk
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Stress min/max note values, zero-like notes, fee boundaries, and mixed transparent/shielded amount distributions.
- Invariant to test: Shielded value, transparent value, and fees must conserve exactly across every accepted boundary case.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Boundary-fuzz all numeric shielded fields through /wallet/scanshieldedtrc20notesbyivk; assert exact conservation and expected rejections.
