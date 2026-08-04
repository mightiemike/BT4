# Q3582: api-surface inconsistency in LogFilter.withContractAddress

## Question
Can an unprivileged attacker invoke the same logical action through /jsonrpc and an alternate HTTP/gRPC/JSON-RPC path so framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java::withContractAddress applies different normalization or guard logic, with the weaker path leading to Execution or state selection against the wrong account or contract context?

## Target
- File/function: framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java::withContractAddress
- Entrypoint: /jsonrpc
- Attacker controls: RPC params, block tags and ranges, topic arrays, filter ids, raw hex, pagination, and visible/base58/hex encoding
- Exploit idea: Cross-check every public surface that can build, simulate, or broadcast the same transaction or query.
- Invariant to test: Equivalent public surfaces must normalize the same fields, enforce the same guards, and return the same decision for one logical action.
- Expected Immunefi impact: Execution or state selection against the wrong account or contract context
- Fast validation: Replay identical inputs through all public surfaces and diff selected owner, contract, tx bytes, and rejection reasons.
