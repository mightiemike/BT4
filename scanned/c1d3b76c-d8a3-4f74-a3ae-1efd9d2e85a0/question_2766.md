# Q2766: underpriced decode-verify work in Rsv.fromSignature

## Question
Can an unprivileged attacker spam /jsonrpc so crypto/src/main/java/org/tron/common/crypto/Rsv.java::fromSignature performs materially underpriced decode, hash, signature, or proof-adjacent work on attacker-controlled input and degrades a production node below true cost?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/Rsv.java::fromSignature
- Entrypoint: /jsonrpc
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Use large-but-valid payloads, nested encodings, repeated expensive decode paths, and malformed-near-valid inputs that maximize work before failure.
- Invariant to test: Public cryptographic and encoding helper work must be bounded and proportionate to the cost or limits visible to the attacker.
- Expected Immunefi impact: Materially underpriced public hash, verify, or decode work
- Fast validation: Benchmark worst valid and near-valid inputs through /jsonrpc; flag cases where CPU or memory growth is attacker-controlled and disproportionate to the request cost.
