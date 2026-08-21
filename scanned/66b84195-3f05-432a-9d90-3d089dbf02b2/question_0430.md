# Q430: CompactEncoder: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `CompactEncoder.unpackToNibbles` in `common/src/main/java/org/tron/common/utils/CompactEncoder.java` — where the attacker feeds CompactEncoder.unpackToNibbles a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that CompactEncoder.unpackToNibbles rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/CompactEncoder.java` -> `CompactEncoder.unpackToNibbles`
- Entrypoint: numeric bytes into CompactEncoder.unpackToNibbles
- Attacker controls: request/transaction/contract inputs to `CompactEncoder.unpackToNibbles` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds CompactEncoder.unpackToNibbles a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: CompactEncoder.unpackToNibbles rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
