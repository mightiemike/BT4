# Q3947: value-fee boundary bug in FullViewingKey.decode

## Question
Can an unprivileged attacker send boundary note values, fees, or mixed transparent/shielded amounts through /wallet/scanshieldedtrc20notesbyivk so framework/src/main/java/org/tron/core/zen/address/FullViewingKey.java::decode misprices or misbalances the flow and reaches Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/FullViewingKey.java::decode
- Entrypoint: /wallet/scanshieldedtrc20notesbyivk
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Stress min/max note values, zero-like notes, fee boundaries, and mixed transparent/shielded amount distributions.
- Invariant to test: Shielded value, transparent value, and fees must conserve exactly across every accepted boundary case.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Boundary-fuzz all numeric shielded fields through /wallet/scanshieldedtrc20notesbyivk; assert exact conservation and expected rejections.
