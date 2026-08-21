# Q3520: StringUtil: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `StringUtil.encode58Check` in `common/src/main/java/org/tron/common/utils/StringUtil.java` — where the attacker feeds StringUtil.encode58Check a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that StringUtil.encode58Check rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/StringUtil.java` -> `StringUtil.encode58Check`
- Entrypoint: numeric bytes into StringUtil.encode58Check
- Attacker controls: request/transaction/contract inputs to `StringUtil.encode58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds StringUtil.encode58Check a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: StringUtil.encode58Check rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
