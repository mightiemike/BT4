# Q2579: DecodeUtil: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `DecodeUtil.addressValid` in `common/src/main/java/org/tron/common/utils/DecodeUtil.java` — where the attacker exploits DecodeUtil.addressValid to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that DecodeUtil.addressValid maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/DecodeUtil.java` -> `DecodeUtil.addressValid`
- Entrypoint: input flowing into DecodeUtil.addressValid
- Attacker controls: request/transaction/contract inputs to `DecodeUtil.addressValid` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits DecodeUtil.addressValid to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: DecodeUtil.addressValid maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
