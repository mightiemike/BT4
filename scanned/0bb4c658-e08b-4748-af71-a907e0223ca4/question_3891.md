# Q3891: merkle-anchor mismatch in ShieldedTRC20ParametersBuilder.getTriggerContractInput

## Question
Can an unprivileged attacker make /wallet/gettriggerinputforshieldedtrc20contract feed framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java::getTriggerContractInput a stale or mismatched Merkle root, voucher, or anchor so spend validity is checked against one tree while settlement touches another, causing Double spend of one shielded note or withdrawal?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java::getTriggerContractInput
- Entrypoint: /wallet/gettriggerinputforshieldedtrc20contract
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Probe boundary blocks, changing anchors, mixed tree versions, and helper APIs that prepare spends or trigger inputs from historical state.
- Invariant to test: The committed tree root/anchor used for verification must be the exact one consumed by settlement and nullifier recording.
- Expected Immunefi impact: Double spend of one shielded note or withdrawal
- Fast validation: Create spends across anchor boundaries via /wallet/gettriggerinputforshieldedtrc20contract; assert the verified anchor and the committed anchor always match.
