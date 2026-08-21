# Q2010: Bloom: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `Bloom.getData` in `chainbase/src/main/java/org/tron/common/bloom/Bloom.java` — where the attacker crafts topics so Bloom.getData bloom/section work grows disproportionately — to break the invariant that Bloom.getData work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/bloom/Bloom.java` -> `Bloom.getData`
- Entrypoint: emit/query events via Bloom.getData
- Attacker controls: request/transaction/contract inputs to `Bloom.getData` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so Bloom.getData bloom/section work grows disproportionately
- Invariant to test: Bloom.getData work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure Bloom.getData cost vs topic count
