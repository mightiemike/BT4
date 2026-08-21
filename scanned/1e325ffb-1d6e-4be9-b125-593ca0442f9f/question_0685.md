# Q685: Bech32: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `Bech32.encode` in `common/src/main/java/org/tron/common/utils/Bech32.java` — where the attacker exploits Bech32.encode to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that Bech32.encode maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Bech32.java` -> `Bech32.encode`
- Entrypoint: input flowing into Bech32.encode
- Attacker controls: request/transaction/contract inputs to `Bech32.encode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits Bech32.encode to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: Bech32.encode maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
