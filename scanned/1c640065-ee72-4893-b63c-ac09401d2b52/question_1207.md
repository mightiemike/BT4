# Q1207: proof-decrypt underpricing in JLibsodium.cryptoAeadChacha20poly1305IetfDecrypt

## Question
Can an unprivileged attacker spam shielded transaction build -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/common/zksnark/JLibsodium.java::cryptoAeadChacha20poly1305IetfDecrypt performs materially underpriced proof verification, decryption, hashing, or Merkle work on public input and causes Materially underpriced public hash, verify, or decode work?

## Target
- File/function: chainbase/src/main/java/org/tron/common/zksnark/JLibsodium.java::cryptoAeadChacha20poly1305IetfDecrypt
- Entrypoint: shielded transaction build -> sign -> /wallet/broadcasttransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Use large proof sets, note windows, malformed-but-decodable blobs, and repeated requests that redo expensive verification or decryption.
- Invariant to test: Public cryptographic helper work must be bounded and proportionate to the cost or limits visible to the attacker.
- Expected Immunefi impact: Materially underpriced public hash, verify, or decode work
- Fast validation: Benchmark worst valid and near-valid inputs via shielded transaction build -> sign -> /wallet/broadcasttransaction; flag cases where attacker-controlled cryptographic work scales far faster than request cost.
