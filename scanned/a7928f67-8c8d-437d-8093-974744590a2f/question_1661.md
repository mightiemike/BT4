# Q1661: cleanup-stuck lifecycle in WitnessCapsule.createDbKey

## Question
Can an unprivileged attacker reach /wallet/accountpermissionupdate -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/WitnessCapsule.java::createDbKey leaves one cancel, withdraw, claim, spend, or unfreeze lifecycle record behind, making the next legal user action impossible and causing Permanent loss of control or freeze of an account or contract configuration?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/WitnessCapsule.java::createDbKey
- Entrypoint: /wallet/accountpermissionupdate -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, permission_id, threshold, signer weights, operations mask, contract address, and signatures
- Exploit idea: Target the transitions that move records from active to completed or canceled, especially when multiple stores track the same lifecycle.
- Invariant to test: Lifecycle completion must cleanly retire or transition every linked record that future legal actions depend on.
- Expected Immunefi impact: Permanent loss of control or freeze of an account or contract configuration
- Fast validation: Run full create-to-complete flows via /wallet/accountpermissionupdate -> sign -> /wallet/broadcasttransaction and assert every active record, index, and balance becomes recoverable and retry-safe afterward.
