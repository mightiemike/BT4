# Q1018: estimate-call bypass in Repository.class-level path

## Question
Can an unprivileged attacker abuse /wallet/estimateenergy so actuator/src/main/java/org/tron/core/vm/repository/Repository.java::class-level path does stateful or unusually expensive work in estimate/call mode that skips a production guard, giving the attacker a cheap public path to Materially underpriced public execution work or stateful simulation bug?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/repository/Repository.java::class-level path
- Entrypoint: /wallet/estimateenergy
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Compare constant-call and estimate paths against full execution to find missing resource, size, or validation checks.
- Invariant to test: Read-only and estimate paths must not mutate state, skip critical guards, or expose materially cheaper access to expensive execution.
- Expected Immunefi impact: Materially underpriced public execution work or stateful simulation bug
- Fast validation: Call the same contract via full execution and estimate/read-only routes; assert identical validation and no hidden side effects on the cheap path.
