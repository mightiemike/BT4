# Q878: signer-threshold confusion in WithdrawExpireUnfreezeParam.getOwnerAddress

## Question
Can an unprivileged attacker use /wallet/withdrawexpireunfreeze -> sign -> /wallet/broadcasttransaction to craft duplicate, reordered, or aliased authorization inputs that make actuator/src/main/java/org/tron/core/vm/nativecontract/param/WithdrawExpireUnfreezeParam.java::getOwnerAddress count signer weight incorrectly, letting one stake, unfreeze, delegate, vote, or reward flow pass without the true threshold and causing Unauthorized unlocking, delegation, or withdrawal of staked TRX/resources?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/nativecontract/param/WithdrawExpireUnfreezeParam.java::getOwnerAddress
- Entrypoint: /wallet/withdrawexpireunfreeze -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/receiver addresses, resource type, amount, unfreeze or withdraw indexes, permission_id, and signatures
- Exploit idea: Stress duplicate signer references, permission_id selection, operations masks, and address alias forms to see whether sign weight is over-counted or the wrong permission branch is used.
- Invariant to test: Signer weight, operations masks, and permission selection must resolve once and only for the intended account/action.
- Expected Immunefi impact: Unauthorized unlocking, delegation, or withdrawal of staked TRX/resources
- Fast validation: Build multi-sign or restricted-permission cases, replay with reordered signers or aliased addresses via /wallet/withdrawexpireunfreeze -> sign -> /wallet/broadcasttransaction, and assert unauthorized payloads still fail.
