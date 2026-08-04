# Q1657: primary-index drift in WitnessCapsule.getAddress

## Question
Can an unprivileged attacker reach /wallet/updateenergylimit -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/WitnessCapsule.java::getAddress updates the primary representation of the account permission tree or contract-owner binding without the matching index or lifecycle view in the effective sign weight or authorized operation set, eventually causing Permanent loss of control or freeze of an account or contract configuration?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/WitnessCapsule.java::getAddress
- Entrypoint: /wallet/updateenergylimit -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, permission_id, threshold, signer weights, operations mask, contract address, and signatures
- Exploit idea: Trace flows that insert, delete, or rewrite the same logical object in more than one store, cache, or capsule.
- Invariant to test: Primary state and every corresponding index/cache must move together or a user must remain able to recover the asset cleanly.
- Expected Immunefi impact: Permanent loss of control or freeze of an account or contract configuration
- Fast validation: Exercise create/update/cancel/withdraw/replay sequences via /wallet/updateenergylimit -> sign -> /wallet/broadcasttransaction, then diff primary records and index views after every step.
