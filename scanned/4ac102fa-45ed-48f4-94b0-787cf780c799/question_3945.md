# Q3945: builder-executor mismatch in FullViewingKey.decode

## Question
Can an unprivileged attacker use shielded transaction build -> sign -> /wallet/broadcasttransaction so framework/src/main/java/org/tron/core/zen/address/FullViewingKey.java::decode builds shielded parameters under assumptions that the later executor does not recheck, allowing a crafted request to reach Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/FullViewingKey.java::decode
- Entrypoint: shielded transaction build -> sign -> /wallet/broadcasttransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Compare parameter builders, trigger-input helpers, note scans, and final settlement for missing consistency checks.
- Invariant to test: Any helper that prepares a shielded action must enforce the same object identity, amount, and context rules as final settlement.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Build one shielded action through all public helper APIs via shielded transaction build -> sign -> /wallet/broadcasttransaction; assert the executor revalidates every security-critical field.
