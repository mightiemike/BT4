# Q2857: Bech32: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `Bech32.encode` in `common/src/main/java/org/tron/common/utils/Bech32.java` — where the attacker supplies bytes that Bech32.encode sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that Bech32.encode treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Bech32.java` -> `Bech32.encode`
- Entrypoint: bytes into Bech32.encode
- Attacker controls: request/transaction/contract inputs to `Bech32.encode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that Bech32.encode sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: Bech32.encode treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
