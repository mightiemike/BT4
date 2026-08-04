# Q563: exceptional-halt lock in MessageCall.getCodeAddress

## Question
Can an unprivileged attacker trigger an exceptional halt through /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction so actuator/src/main/java/org/tron/core/vm/MessageCall.java::getCodeAddress leaves a contract, account, or note in a half-advanced lifecycle state that cannot be legally completed or reversed, resulting in Permanent contract or user-fund lock from broken cleanup?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/MessageCall.java::getCodeAddress
- Entrypoint: /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Focus on obligations, escrow-like flows, delegated-resource native opcodes, and lifecycle transitions that span multiple internal structures.
- Invariant to test: Exceptional halts must either leave the lifecycle untouched or leave a recoverable state; they must not strand value.
- Expected Immunefi impact: Permanent contract or user-fund lock from broken cleanup
- Fast validation: Force halts at each stage of the lifecycle via /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction and assert users can still fully recover or retry the affected asset/state.
