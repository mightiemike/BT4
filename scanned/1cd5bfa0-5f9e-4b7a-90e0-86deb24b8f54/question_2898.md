# Q2898: underpriced decode-verify work in ECKeyFactory.getInstance

## Question
Can an unprivileged attacker spam /jsonrpc eth_sendRawTransaction so crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance performs materially underpriced decode, hash, signature, or proof-adjacent work on attacker-controlled input and degrades a production node below true cost?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/jce/ECKeyFactory.java::getInstance
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Use large-but-valid payloads, nested encodings, repeated expensive decode paths, and malformed-near-valid inputs that maximize work before failure.
- Invariant to test: Public cryptographic and encoding helper work must be bounded and proportionate to the cost or limits visible to the attacker.
- Expected Immunefi impact: Materially underpriced public hash, verify, or decode work
- Fast validation: Benchmark worst valid and near-valid inputs through /jsonrpc eth_sendRawTransaction; flag cases where CPU or memory growth is attacker-controlled and disproportionate to the request cost.
