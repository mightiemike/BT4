# Q2551: call-depth cleanup bug in DataWord.hashCode

## Question
Can an unprivileged attacker use /wallet/estimateenergy to push call depth, recursion, or nested create/call structure into a path where common/src/main/java/org/tron/common/runtime/vm/DataWord.java::hashCode forgets to clean up TVM storage, balances, or repository state or receipts, refunds, internal transfers, or log state, leading to Permanent contract or user-fund lock from broken cleanup?

## Target
- File/function: common/src/main/java/org/tron/common/runtime/vm/DataWord.java::hashCode
- Entrypoint: /wallet/estimateenergy
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Create deeply nested or mutually recursive calls that hit limits only after temporary state and accounting structures are populated.
- Invariant to test: Depth limits and nested-frame exits must leave no surviving garbage or authorization/accounting residue in TVM storage, balances, or repository state/receipts, refunds, internal transfers, or log state.
- Expected Immunefi impact: Permanent contract or user-fund lock from broken cleanup
- Fast validation: Deploy contracts that hit depth and recursion edges via /wallet/estimateenergy, then assert no stale storage, call-context, or balance artifacts survive.
