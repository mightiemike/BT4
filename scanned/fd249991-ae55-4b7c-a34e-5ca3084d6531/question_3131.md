# Q3131: value-fee boundary bug in ZksnarkClient.checkZksnarkProof

## Question
Can an unprivileged attacker send boundary note values, fees, or mixed transparent/shielded amounts through /wallet/gettriggerinputforshieldedtrc20contract so framework/src/main/java/org/tron/common/zksnark/ZksnarkClient.java::checkZksnarkProof misprices or misbalances the flow and reaches Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: framework/src/main/java/org/tron/common/zksnark/ZksnarkClient.java::checkZksnarkProof
- Entrypoint: /wallet/gettriggerinputforshieldedtrc20contract
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Stress min/max note values, zero-like notes, fee boundaries, and mixed transparent/shielded amount distributions.
- Invariant to test: Shielded value, transparent value, and fees must conserve exactly across every accepted boundary case.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Boundary-fuzz all numeric shielded fields through /wallet/gettriggerinputforshieldedtrc20contract; assert exact conservation and expected rejections.
