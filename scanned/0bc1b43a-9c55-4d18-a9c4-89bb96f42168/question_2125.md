# Q2125: DecodeUtil: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `DecodeUtil.addressValid` in `common/src/main/java/org/tron/common/utils/DecodeUtil.java` — where the attacker feeds DecodeUtil.addressValid a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that DecodeUtil.addressValid rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/DecodeUtil.java` -> `DecodeUtil.addressValid`
- Entrypoint: numeric bytes into DecodeUtil.addressValid
- Attacker controls: request/transaction/contract inputs to `DecodeUtil.addressValid` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds DecodeUtil.addressValid a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: DecodeUtil.addressValid rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
