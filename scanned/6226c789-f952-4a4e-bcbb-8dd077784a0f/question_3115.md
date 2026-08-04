# Q3115: call-depth cleanup bug in RuntimeImpl.execute

## Question
Can an unprivileged attacker use /wallet/triggerconstantcontract to push call depth, recursion, or nested create/call structure into a path where framework/src/main/java/org/tron/common/runtime/RuntimeImpl.java::execute forgets to clean up TVM storage, balances, or repository state or receipts, refunds, internal transfers, or log state, leading to Permanent contract or user-fund lock from broken cleanup?

## Target
- File/function: framework/src/main/java/org/tron/common/runtime/RuntimeImpl.java::execute
- Entrypoint: /wallet/triggerconstantcontract
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Create deeply nested or mutually recursive calls that hit limits only after temporary state and accounting structures are populated.
- Invariant to test: Depth limits and nested-frame exits must leave no surviving garbage or authorization/accounting residue in TVM storage, balances, or repository state/receipts, refunds, internal transfers, or log state.
- Expected Immunefi impact: Permanent contract or user-fund lock from broken cleanup
- Fast validation: Deploy contracts that hit depth and recursion edges via /wallet/triggerconstantcontract, then assert no stale storage, call-context, or balance artifacts survive.
