# Q3356: shielded-query underpricing in RpcApiService.scanNoteByIvk

## Question
Can an unprivileged attacker abuse gRPC WalletApi / WalletSolidityApi so framework/src/main/java/org/tron/core/services/RpcApiService.java::scanNoteByIvk performs expensive note scanning, trigger-input assembly, or decryption on attacker-controlled windows below true cost, leading to Materially underpriced public proof, note-scan, or decryption work?

## Target
- File/function: framework/src/main/java/org/tron/core/services/RpcApiService.java::scanNoteByIvk
- Entrypoint: gRPC WalletApi / WalletSolidityApi
- Attacker controls: RPC params, block tags and ranges, topic arrays, filter ids, raw hex, pagination, and visible/base58/hex encoding
- Exploit idea: Use large note windows, malformed but decodable note data, and repeated scans that force the same decryption or proof preparation work.
- Invariant to test: Public shielded helper APIs must bound expensive per-request work and must not let an external user amplify decryption or trigger-building costs.
- Expected Immunefi impact: Materially underpriced public proof, note-scan, or decryption work
- Fast validation: Benchmark shielded helper endpoints via gRPC WalletApi / WalletSolidityApi; identify requests where cost scales with chain or note history far beyond request cost.
