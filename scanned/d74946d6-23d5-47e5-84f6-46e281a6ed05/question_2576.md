# Q2576: Commons: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `Commons.decode58Check` in `chainbase/src/main/java/org/tron/common/utils/Commons.java` — where the attacker exploits Commons.decode58Check to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that Commons.decode58Check maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/Commons.java` -> `Commons.decode58Check`
- Entrypoint: input flowing into Commons.decode58Check
- Attacker controls: request/transaction/contract inputs to `Commons.decode58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits Commons.decode58Check to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: Commons.decode58Check maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
