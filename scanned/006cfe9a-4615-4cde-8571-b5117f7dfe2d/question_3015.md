# Q3015: merkle-anchor mismatch in BN128G2.create

## Question
Can an unprivileged attacker make /wallet/scanshieldedtrc20notesbyivk feed crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java::create a stale or mismatched Merkle root, voucher, or anchor so spend validity is checked against one tree while settlement touches another, causing Reused proof, signature, or encoded identifier accepted more than once?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/zksnark/BN128G2.java::create
- Entrypoint: /wallet/scanshieldedtrc20notesbyivk
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Probe boundary blocks, changing anchors, mixed tree versions, and helper APIs that prepare spends or trigger inputs from historical state.
- Invariant to test: The committed tree root/anchor used for verification must be the exact one consumed by settlement and nullifier recording.
- Expected Immunefi impact: Reused proof, signature, or encoded identifier accepted more than once
- Fast validation: Create spends across anchor boundaries via /wallet/scanshieldedtrc20notesbyivk; assert the verified anchor and the committed anchor always match.
