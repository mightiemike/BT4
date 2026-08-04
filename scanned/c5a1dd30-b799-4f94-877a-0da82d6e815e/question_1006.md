# Q1006: estimate-call bypass in ProgramInvokeImpl.getCallerAddress

## Question
Can an unprivileged attacker abuse /wallet/triggerconstantcontract so actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeImpl.java::getCallerAddress does stateful or unusually expensive work in estimate/call mode that skips a production guard, giving the attacker a cheap public path to Materially underpriced public execution work or stateful simulation bug?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeImpl.java::getCallerAddress
- Entrypoint: /wallet/triggerconstantcontract
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Compare constant-call and estimate paths against full execution to find missing resource, size, or validation checks.
- Invariant to test: Read-only and estimate paths must not mutate state, skip critical guards, or expose materially cheaper access to expensive execution.
- Expected Immunefi impact: Materially underpriced public execution work or stateful simulation bug
- Fast validation: Call the same contract via full execution and estimate/read-only routes; assert identical validation and no hidden side effects on the cheap path.
