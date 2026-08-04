# Q1337: cleanup-stuck lifecycle in AssetIssueCapsule.createDbKeyString

## Question
Can an unprivileged attacker reach /wallet/createtransaction -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java::createDbKeyString leaves one cancel, withdraw, claim, spend, or unfreeze lifecycle record behind, making the next legal user action impossible and causing Permanent lock or misaccounting of transferred value?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java::createDbKeyString
- Entrypoint: /wallet/createtransaction -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/to addresses, amount, asset id, permission_id, signatures, and visible/base58/hex encoding
- Exploit idea: Target the transitions that move records from active to completed or canceled, especially when multiple stores track the same lifecycle.
- Invariant to test: Lifecycle completion must cleanly retire or transition every linked record that future legal actions depend on.
- Expected Immunefi impact: Permanent lock or misaccounting of transferred value
- Fast validation: Run full create-to-complete flows via /wallet/createtransaction -> sign -> /wallet/broadcasttransaction and assert every active record, index, and balance becomes recoverable and retry-safe afterward.
