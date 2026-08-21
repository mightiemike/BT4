# Q2328: Bloom: node info disclosure

## Question
Can an unprivileged attacker (smart-contract/query) abuse `Bloom.getLowBits` in `chainbase/src/main/java/org/tron/common/bloom/Bloom.java` — where the attacker queries Bloom.getLowBits to read node internals that aid a further in-scope attack — to break the invariant that Bloom.getLowBits exposes no sensitive internal state to anonymous callers, leading to: Information disclosure (in-scope only if it enables impact)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/bloom/Bloom.java` -> `Bloom.getLowBits`
- Entrypoint: anonymous query to Bloom.getLowBits
- Attacker controls: request/transaction/contract inputs to `Bloom.getLowBits` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: queries Bloom.getLowBits to read node internals that aid a further in-scope attack
- Invariant to test: Bloom.getLowBits exposes no sensitive internal state to anonymous callers
- Expected Immunefi impact: Information disclosure (in-scope only if it enables impact)
- Fast validation: assert Bloom.getLowBits response omits sensitive fields
