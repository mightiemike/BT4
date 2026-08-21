# Q3096: UpdateAssetActuator: fee accounting bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAssetActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` — where the attacker structures UpdateAssetActuator so UpdateAssetActuator.calcFee returns less than the resource actually consumed by UpdateAssetActuator.execute — to break the invariant that fee charged is >= real resource consumed for UpdateAssetActuator, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` -> `UpdateAssetActuator.calcFee`
- Entrypoint: broadcast UpdateAssetActuator shaped to minimize calcFee
- Attacker controls: request/transaction/contract inputs to `UpdateAssetActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: structures UpdateAssetActuator so UpdateAssetActuator.calcFee returns less than the resource actually consumed by UpdateAssetActuator.execute
- Invariant to test: fee charged is >= real resource consumed for UpdateAssetActuator
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: compare calcFee to measured bandwidth/energy of execute
