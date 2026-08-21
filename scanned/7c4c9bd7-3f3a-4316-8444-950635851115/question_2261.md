# Q2261: DBIterator: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `DBIterator.<primary method>` in `chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java` — where the attacker crafts a key consumed by DBIterator.<primary method> that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in DBIterator.<primary method> are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java` -> `DBIterator.<primary method>`
- Entrypoint: write via a path using DBIterator.<primary method>
- Attacker controls: request/transaction/contract inputs to `DBIterator.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by DBIterator.<primary method> that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in DBIterator.<primary method> are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
