# Q3079: proof-decrypt underpricing in Fp6.hashCode

## Question
Can an unprivileged attacker spam /wallet/validateaddress so crypto/src/main/java/org/tron/common/crypto/zksnark/Fp6.java::hashCode performs materially underpriced proof verification, decryption, hashing, or Merkle work on public input and causes Materially underpriced public hash, verify, or decode work?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/zksnark/Fp6.java::hashCode
- Entrypoint: /wallet/validateaddress
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Use large proof sets, note windows, malformed-but-decodable blobs, and repeated requests that redo expensive verification or decryption.
- Invariant to test: Public cryptographic helper work must be bounded and proportionate to the cost or limits visible to the attacker.
- Expected Immunefi impact: Materially underpriced public hash, verify, or decode work
- Fast validation: Benchmark worst valid and near-valid inputs via /wallet/validateaddress; flag cases where attacker-controlled cryptographic work scales far faster than request cost.
