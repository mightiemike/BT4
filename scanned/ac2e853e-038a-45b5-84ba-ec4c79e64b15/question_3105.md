# Q3105: builder-executor mismatch in Params.class-level path

## Question
Can an unprivileged attacker use /wallet/validateaddress so crypto/src/main/java/org/tron/common/crypto/zksnark/Params.java::class-level path builds shielded parameters under assumptions that the later executor does not recheck, allowing a crafted request to reach Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/zksnark/Params.java::class-level path
- Entrypoint: /wallet/validateaddress
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Compare parameter builders, trigger-input helpers, note scans, and final settlement for missing consistency checks.
- Invariant to test: Any helper that prepares a shielded action must enforce the same object identity, amount, and context rules as final settlement.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Build one shielded action through all public helper APIs via /wallet/validateaddress; assert the executor revalidates every security-critical field.
