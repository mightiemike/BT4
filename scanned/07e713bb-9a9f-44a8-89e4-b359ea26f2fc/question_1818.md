# Q1818: Manager: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `Manager.needToSetBlackholePermission` in `framework/src/main/java/org/tron/core/db/Manager.java` — where the attacker replays a transaction past its intended window because Manager.needToSetBlackholePermission mis-checks expiration or ref-block — to break the invariant that Manager.needToSetBlackholePermission rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/Manager.java` -> `Manager.needToSetBlackholePermission`
- Entrypoint: rebroadcast a tx through Manager.needToSetBlackholePermission
- Attacker controls: request/transaction/contract inputs to `Manager.needToSetBlackholePermission` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because Manager.needToSetBlackholePermission mis-checks expiration or ref-block
- Invariant to test: Manager.needToSetBlackholePermission rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
