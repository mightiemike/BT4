# Q1142: signer-threshold confusion in Commons.decode58Check

## Question
Can an unprivileged attacker use /wallet/broadcasttransaction to craft duplicate, reordered, or aliased authorization inputs that make chainbase/src/main/java/org/tron/common/utils/Commons.java::decode58Check count signer weight incorrectly, letting one public transaction-processing flow pass without the true threshold and causing Unauthorized transaction execution or state mutation?

## Target
- File/function: chainbase/src/main/java/org/tron/common/utils/Commons.java::decode58Check
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Stress duplicate signer references, permission_id selection, operations masks, and address alias forms to see whether sign weight is over-counted or the wrong permission branch is used.
- Invariant to test: Signer weight, operations masks, and permission selection must resolve once and only for the intended account/action.
- Expected Immunefi impact: Unauthorized transaction execution or state mutation
- Fast validation: Build multi-sign or restricted-permission cases, replay with reordered signers or aliased addresses via /wallet/broadcasttransaction, and assert unauthorized payloads still fail.
