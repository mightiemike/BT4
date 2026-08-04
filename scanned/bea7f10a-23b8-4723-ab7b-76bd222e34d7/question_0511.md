# Q511: call-depth cleanup bug in ChainParameterEnum.fromCode

## Question
Can an unprivileged attacker use /wallet/broadcasttransaction to push call depth, recursion, or nested create/call structure into a path where actuator/src/main/java/org/tron/core/vm/ChainParameterEnum.java::fromCode forgets to clean up transaction-processing state or the resulting accounting, receipt, or index state, leading to Permanent contract or user-fund lock from broken cleanup?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/ChainParameterEnum.java::fromCode
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Create deeply nested or mutually recursive calls that hit limits only after temporary state and accounting structures are populated.
- Invariant to test: Depth limits and nested-frame exits must leave no surviving garbage or authorization/accounting residue in transaction-processing state/the resulting accounting, receipt, or index state.
- Expected Immunefi impact: Permanent contract or user-fund lock from broken cleanup
- Fast validation: Deploy contracts that hit depth and recursion edges via /wallet/broadcasttransaction, then assert no stale storage, call-context, or balance artifacts survive.
