# Q870: boundary-value exploit in VoteWitnessParam.getVoterAddress

## Question
Can an unprivileged attacker send boundary values through /wallet/votewitnessaccount -> sign -> /wallet/broadcasttransaction so actuator/src/main/java/org/tron/core/vm/nativecontract/param/VoteWitnessParam.java::getVoterAddress mishandles zero, min, max, sign, precision, or dust cases, breaking the accounting relationship between frozen balances, delegated resources, or reward state and withdrawable amounts, vote weight, or receiver entitlements and leading to Unauthorized unlocking, delegation, or withdrawal of staked TRX/resources?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/nativecontract/param/VoteWitnessParam.java::getVoterAddress
- Entrypoint: /wallet/votewitnessaccount -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/receiver addresses, resource type, amount, unfreeze or withdraw indexes, permission_id, and signatures
- Exploit idea: Exercise min/max integers, exact threshold values, zero-like payloads, dust, and values around public protocol constraints to find off-by-one or overflow paths.
- Invariant to test: Protocol boundaries must reject or handle edge values without changing frozen balances, delegated resources, or reward state or withdrawable amounts, vote weight, or receiver entitlements inconsistently.
- Expected Immunefi impact: Unauthorized unlocking, delegation, or withdrawal of staked TRX/resources
- Fast validation: Run boundary fuzzing against all numeric fields reachable from /wallet/votewitnessaccount -> sign -> /wallet/broadcasttransaction and assert post-state conservation plus expected rejection behavior.
