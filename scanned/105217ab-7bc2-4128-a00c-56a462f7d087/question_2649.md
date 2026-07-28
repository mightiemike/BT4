# Q2649: Event cleanup delete - cleanup horizon race overwrite

## Question
If a user create a public Push-chain action that produces a pending outbound observed by the Universal Client, can `DeleteTerminalEvents` be pushed into a path where the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried causes it to overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload, so that cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/common/chain_store.go:DeleteTerminalEvents
- Entrypoint: create a public Push-chain action that produces a pending outbound observed by the Universal Client
- Attacker controls: the retention window, chain height, and wall-clock timing that determine when rows are cleaned up or retried
- Exploit idea: overwrite a nonterminal row with stale or conflicting state so later logic votes or signs the wrong event payload
- Invariant to test: cleanup never removes an event that still needs signing, broadcasting, resolving, or refund voting
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: replay the same inbound or outbound and verify every state transition is idempotent rather than generating conflicting rows
