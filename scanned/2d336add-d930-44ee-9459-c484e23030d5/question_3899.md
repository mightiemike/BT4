# Q3899: value-fee boundary bug in ShieldedTRC20ParametersBuilder.createSpendAuth

## Question
Can an unprivileged attacker send boundary note values, fees, or mixed transparent/shielded amounts through /wallet/scanshieldedtrc20notesbyovk so framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java::createSpendAuth misprices or misbalances the flow and reaches Unauthorized shielded spend or note theft?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java::createSpendAuth
- Entrypoint: /wallet/scanshieldedtrc20notesbyovk
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Stress min/max note values, zero-like notes, fee boundaries, and mixed transparent/shielded amount distributions.
- Invariant to test: Shielded value, transparent value, and fees must conserve exactly across every accepted boundary case.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Boundary-fuzz all numeric shielded fields through /wallet/scanshieldedtrc20notesbyovk; assert exact conservation and expected rejections.
