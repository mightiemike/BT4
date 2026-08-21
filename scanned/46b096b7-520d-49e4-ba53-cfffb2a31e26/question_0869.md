# Q869: Bloom: node info disclosure

## Question
Can an unprivileged attacker (smart-contract/query) abuse `Bloom.getData` in `chainbase/src/main/java/org/tron/common/bloom/Bloom.java` — where the attacker queries Bloom.getData to read node internals that aid a further in-scope attack — to break the invariant that Bloom.getData exposes no sensitive internal state to anonymous callers, leading to: Information disclosure (in-scope only if it enables impact)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/bloom/Bloom.java` -> `Bloom.getData`
- Entrypoint: anonymous query to Bloom.getData
- Attacker controls: request/transaction/contract inputs to `Bloom.getData` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: queries Bloom.getData to read node internals that aid a further in-scope attack
- Invariant to test: Bloom.getData exposes no sensitive internal state to anonymous callers
- Expected Immunefi impact: Information disclosure (in-scope only if it enables impact)
- Fast validation: assert Bloom.getData response omits sensitive fields
