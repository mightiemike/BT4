# Q880: Bloom: bloom/topic amplification

## Question
Can an unprivileged attacker (smart-contract/query) abuse `Bloom.getLowBits` in `chainbase/src/main/java/org/tron/common/bloom/Bloom.java` — where the attacker crafts topics so Bloom.getLowBits bloom/section work grows disproportionately — to break the invariant that Bloom.getLowBits work is bounded per event regardless of topic content, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/bloom/Bloom.java` -> `Bloom.getLowBits`
- Entrypoint: emit/query events via Bloom.getLowBits
- Attacker controls: request/transaction/contract inputs to `Bloom.getLowBits` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts topics so Bloom.getLowBits bloom/section work grows disproportionately
- Invariant to test: Bloom.getLowBits work is bounded per event regardless of topic content
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: measure Bloom.getLowBits cost vs topic count
