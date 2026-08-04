# Q1146: boundary-value exploit in Commons.decode58Check

## Question
Can an unprivileged attacker send boundary values through gRPC broadcastTransaction so chainbase/src/main/java/org/tron/common/utils/Commons.java::decode58Check mishandles zero, min, max, sign, precision, or dust cases, breaking the accounting relationship between transaction-processing state and the resulting accounting, receipt, or index state and leading to Unauthorized transaction execution or state mutation?

## Target
- File/function: chainbase/src/main/java/org/tron/common/utils/Commons.java::decode58Check
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Exercise min/max integers, exact threshold values, zero-like payloads, dust, and values around public protocol constraints to find off-by-one or overflow paths.
- Invariant to test: Protocol boundaries must reject or handle edge values without changing transaction-processing state or the resulting accounting, receipt, or index state inconsistently.
- Expected Immunefi impact: Unauthorized transaction execution or state mutation
- Fast validation: Run boundary fuzzing against all numeric fields reachable from gRPC broadcastTransaction and assert post-state conservation plus expected rejection behavior.
