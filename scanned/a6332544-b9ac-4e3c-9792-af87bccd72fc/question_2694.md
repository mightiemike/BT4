# Q2694: boundary-value exploit in StringUtil.createDbKey

## Question
Can an unprivileged attacker send boundary values through /jsonrpc eth_sendRawTransaction so common/src/main/java/org/tron/common/utils/StringUtil.java::createDbKey mishandles zero, min, max, sign, precision, or dust cases, breaking the accounting relationship between transaction-processing state and the resulting accounting, receipt, or index state and leading to Unauthorized transaction execution or state mutation?

## Target
- File/function: common/src/main/java/org/tron/common/utils/StringUtil.java::createDbKey
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Exercise min/max integers, exact threshold values, zero-like payloads, dust, and values around public protocol constraints to find off-by-one or overflow paths.
- Invariant to test: Protocol boundaries must reject or handle edge values without changing transaction-processing state or the resulting accounting, receipt, or index state inconsistently.
- Expected Immunefi impact: Unauthorized transaction execution or state mutation
- Fast validation: Run boundary fuzzing against all numeric fields reachable from /jsonrpc eth_sendRawTransaction and assert post-state conservation plus expected rejection behavior.
