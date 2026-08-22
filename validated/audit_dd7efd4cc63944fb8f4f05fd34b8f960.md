### Title
Front-Running Token Name Squatting via Missing Owner-Binding Check in `AssetIssueActuator` (`AllowSameTokenName == 0` Mode) - (File: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java`)

### Summary
When the `AllowSameTokenName` dynamic parameter is `0`, `AssetIssueActuator` enforces asset-name uniqueness purely on the raw `name` field with no binding to the intended issuer's address. Any account can observe a pending `AssetIssueContract` broadcast in the mempool, extract the target `name`, and submit their own asset-issuance transaction with that exact name first, causing the legitimate issuer's later transaction to fail validation with `"Token exists"`. This mirrors the reported bug class: a shared, globally-unique key (`sessionKey` in the report, asset `name` here) is claimed on a first-come basis with no check that the claimer matches the intended beneficiary, enabling griefing/DoS against the rightful transaction submitter.

### Finding Description
`AssetIssueActuator.validate()` checks name uniqueness solely against the store, independent of `ownerAddress`: [1](#0-0) 

This check is not atomically tied to the specific submitter — any transaction with a matching `name` succeeds in claiming the slot regardless of who submits it, exactly like the `CredibleAccountModule.sessionKeyToWallet` mapping in the external report where `enableSessionKey()` checked only `sessionKeyToWallet[rl.sessionKey] != address(0)` without validating that the caller is the intended (pre-authorized) wallet. In `AssetIssueActuator`, the constraint applies once `getAllowSameTokenName() == 0`: [2](#0-1) 

An attacker monitoring the mempool for a broadcast `AssetIssueContract` can extract the plaintext `name` (transactions are unencrypted and visible before confirmation), craft their own `AssetIssueContract` with the identical `name`, and get it included in an earlier block position (e.g., via a node they control, or simply by broadcasting with better propagation timing). Once the attacker's asset with that `name` is committed, the legitimate issuer's original transaction will fail at: [1](#0-0) 

with `"Token exists"`, permanently denying the legitimate issuer that specific asset `name` — because once created, an asset's name cannot be reused by anyone else under this legacy mode, and `AssetIssueActuator` allows at most one asset issuance per account (`"An account can only issue one asset"`), so the griefed party cannot simply retry with the same identity/name combination. [3](#0-2) 

### Impact Explanation
This is a griefing/DoS vector: a legitimate issuer who has pre-announced or leaked (via mempool visibility) their intended token name can be permanently blocked from issuing that asset, wasting their issuance fee-eligible transaction attempt and any off-chain preparation (exchange listings, marketing, `FrozenSupply` schedules tied to that name) built around the specific name. This is the direct on-chain analog of the reported issue's impact ("Griefing SCWs by attackers, preventing them from enabling their `sessionKeys` for usage").

### Likelihood Explanation
Exploitability is gated entirely on `DynamicPropertiesStore.getAllowSameTokenName() == 0`. This value is governed by a committee proposal (`ProposalUtil`, `ProposalService`) and on TRON mainnet has historically been switched to `1` via committee vote, which would make the uniqueness check (and thus this specific front-run vector) largely inert on mainnet today. However, the vulnerable code path remains live and reachable on any deployment (private chain, testnet, or a mainnet state prior/rollback to `AllowSameTokenName == 0`), and the check itself has no owner-binding regardless of the flag's value — making this a structural weakness in the actuator logic rather than a purely historical artifact.

### Recommendation
Do not rely solely on global-name existence checks decoupled from the submitting/intended owner. If asset-name uniqueness must be enforced, consider binding name reservations to a specific pre-committed owner address (e.g., via a commit-reveal scheme, or checking that a prior "reservation" transaction from the same `ownerAddress` exists) before allowing final issuance, so an unrelated third party cannot preemptively claim a name intended for another account purely by observing it in the mempool.

### Proof of Concept
1. Legitimate user A broadcasts `AssetIssueContract` with `name = "MYCOIN"`, `AllowSameTokenName == 0`.
2. Attacker B observes the pending transaction in the mempool/P2P layer and extracts `name = "MYCOIN"`.
3. Attacker B broadcasts their own `AssetIssueContract` with the same `name = "MYCOIN"` and gets it confirmed in an earlier block (or same block with earlier ordering).
4. Legitimate user A's transaction now fails `AssetIssueActuator.validate()` at the `assetIssueStore.get(name) != null` check with `"Token exists"`. [1](#0-0) 
5. Because each account may only issue one asset (`"An account can only issue one asset"` check), user A cannot retry issuance under the same name from any account once it has been claimed by B.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L78-87)
```java
      if (dynamicStore.getAllowSameTokenName() == 0) {
        assetIssueCapsuleV2.setPrecision(0);
        assetIssueStore
            .put(assetIssueCapsule.createDbKey(), assetIssueCapsule);
        assetIssueV2Store
            .put(assetIssueCapsuleV2.createDbV2Key(), assetIssueCapsuleV2);
      } else {
        assetIssueV2Store
            .put(assetIssueCapsuleV2.createDbV2Key(), assetIssueCapsuleV2);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L210-214)
```java
    if (dynamicStore.getAllowSameTokenName() == 0
        && assetIssueStore.get(assetIssueContract.getName().toByteArray())
        != null) {
      throw new ContractValidateException("Token exists");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L287-289)
```java
    if (!accountCapsule.getAssetIssuedName().isEmpty()) {
      throw new ContractValidateException("An account can only issue one asset");
    }
```
