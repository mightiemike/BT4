# Q3951: merkle-anchor mismatch in IncomingViewingKey.address

## Question
Can an unprivileged attacker make /jsonrpc eth_sendRawTransaction feed framework/src/main/java/org/tron/core/zen/address/IncomingViewingKey.java::address a stale or mismatched Merkle root, voucher, or anchor so spend validity is checked against one tree while settlement touches another, causing Reused proof, signature, or encoded identifier accepted more than once?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/IncomingViewingKey.java::address
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Probe boundary blocks, changing anchors, mixed tree versions, and helper APIs that prepare spends or trigger inputs from historical state.
- Invariant to test: The committed tree root/anchor used for verification must be the exact one consumed by settlement and nullifier recording.
- Expected Immunefi impact: Reused proof, signature, or encoded identifier accepted more than once
- Fast validation: Create spends across anchor boundaries via /jsonrpc eth_sendRawTransaction; assert the verified anchor and the committed anchor always match.
