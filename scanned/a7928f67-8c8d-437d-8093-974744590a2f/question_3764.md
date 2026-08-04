# Q3764: shielded-query underpricing in RuntimeData.getRemoteAddr

## Question
Can an unprivileged attacker abuse /wallet/triggersmartcontract -> sign -> /wallet/broadcasttransaction so framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr performs expensive note scanning, trigger-input assembly, or decryption on attacker-controlled windows below true cost, leading to Materially underpriced public proof, note-scan, or decryption work?

## Target
- File/function: framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java::getRemoteAddr
- Entrypoint: /wallet/triggersmartcontract -> sign -> /wallet/broadcasttransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Use large note windows, malformed but decodable note data, and repeated scans that force the same decryption or proof preparation work.
- Invariant to test: Public shielded helper APIs must bound expensive per-request work and must not let an external user amplify decryption or trigger-building costs.
- Expected Immunefi impact: Materially underpriced public proof, note-scan, or decryption work
- Fast validation: Benchmark shielded helper endpoints via /wallet/triggersmartcontract -> sign -> /wallet/broadcasttransaction; identify requests where cost scales with chain or note history far beyond request cost.
