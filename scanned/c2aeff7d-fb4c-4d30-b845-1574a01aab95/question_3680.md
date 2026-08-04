# Q3680: shielded-query underpricing in TransactionReceipt.class-level path

## Question
Can an unprivileged attacker abuse /wallet/broadcasthex so framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java::class-level path performs expensive note scanning, trigger-input assembly, or decryption on attacker-controlled windows below true cost, leading to Materially underpriced public proof, note-scan, or decryption work?

## Target
- File/function: framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java::class-level path
- Entrypoint: /wallet/broadcasthex
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Use large note windows, malformed but decodable note data, and repeated scans that force the same decryption or proof preparation work.
- Invariant to test: Public shielded helper APIs must bound expensive per-request work and must not let an external user amplify decryption or trigger-building costs.
- Expected Immunefi impact: Materially underpriced public proof, note-scan, or decryption work
- Fast validation: Benchmark shielded helper endpoints via /wallet/broadcasthex; identify requests where cost scales with chain or note history far beyond request cost.
