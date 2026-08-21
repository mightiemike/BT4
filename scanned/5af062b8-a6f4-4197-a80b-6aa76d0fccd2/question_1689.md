# Q1689: DecodeUtil: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `DecodeUtil.addressValid` in `common/src/main/java/org/tron/common/utils/DecodeUtil.java` — where the attacker supplies bytes that DecodeUtil.addressValid sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that DecodeUtil.addressValid treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/DecodeUtil.java` -> `DecodeUtil.addressValid`
- Entrypoint: bytes into DecodeUtil.addressValid
- Attacker controls: request/transaction/contract inputs to `DecodeUtil.addressValid` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that DecodeUtil.addressValid sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: DecodeUtil.addressValid treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
