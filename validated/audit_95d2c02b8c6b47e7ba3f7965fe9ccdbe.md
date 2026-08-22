### Title
Permissionless TRC10 asset issuance with no name-ownership/allowlist check enables token-name squatting and spoofing of "official" assets - (File: actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java)

### Summary
`AssetIssueActuator` allows any funded account to issue a TRC10 token using an unguarded, globally incrementing counter (`DynamicPropertiesStore.getTokenIdNum()`) to mint a new asset ID, with no allowlist or authorization check tying the `name`/`abbr` to any legitimate/official issuer. This is the same bug class as the reported Solana off-ramp issue: a shared, permissionless "program" (here, the AssetIssue system contract) lets any signer stand up a new "official-looking" instance (a token with a brand name such as `USDT`, `BTC`, an exchange name, etc.) under the same protocol identity, with no `authorized_initializer`/allowlist gate.

### Finding Description
`execute()` derives the new asset's ID purely from an unguarded global counter and persists it without any check that the caller is authorized to use the requested `name`/`abbr`: [1](#0-0) 

`doValidate()` only rejects a duplicate `name` when `AllowSameTokenName == 0` (legacy mode); once same-token-name is allowed (the mode enforced going forward, since only the numeric `id` is the canonical identifier), *any* account can issue a token with an already-used or brand-mimicking `name`/`abbr` — the only checks are generic format checks (`validAssetName`, `validAssetName` for abbr, `trx` reserved-word check), not identity/ownership of the brand: [2](#0-1) [3](#0-2) [4](#0-3) 

The only per-account restriction is a one-asset-per-account limit, which does nothing to prevent an attacker's separate account from re-using an established or trusted brand name/abbreviation: [5](#0-4) 

This mirrors the Solana report exactly: the `off_ramp_counter`/`OffRampState` in the report is unguarded and lets anyone mint a new "official-looking" state under the shared program ID; here, `tokenIdNum`/`AssetIssueContract` is unguarded and lets anyone mint a new "official-looking" TRC10 asset under the shared, canonical AssetIssue system contract, with no `GlobalConfig`/allowlist step to gate who may claim a given brand name.

The same unguarded-counter pattern (no admin/allowlist tie) also exists in `ExchangeCreateActuator`, which mints exchange pair IDs from `dynamicStore.getLatestExchangeNum()` with no check on who may create a market for arbitrary token pairs: [6](#0-5) 

### Impact Explanation
Any account holding sufficient TRX for the `AssetIssueFee` can broadcast an `AssetIssueContract` transaction and mint a token whose `name`/`abbr` impersonates a well-known/official project, then distribute or list it as if it were the legitimate asset. Because the system does not tie brand identity to an authorized issuer list, this enables scams/fraud analogous to the reported off-ramp spoofing — victims interacting with a fake "official" token or exchange pair believe they are dealing with the legitimate issuer, leading to potential financial loss (an unauthorized-account-operation / asset-integrity class issue, not merely cosmetic).

### Likelihood Explanation
High reachability: this is triggerable by any anonymous account via a standard broadcast transaction (`AssetIssueContract`), requiring only enough TRX to cover `calcFee()` (the asset issue fee) and passing basic format validation — no privileged role, no leaked key, and no node/P2P compromise needed.

### Recommendation
Introduce an issuer/brand allowlist or verified-name registry (analogous to the recommended `GlobalConfig`/`authorized_initializer` in the report) so that reserved or previously-claimed `name`/`abbr` values cannot be reused by unrelated accounts, and/or require a reservation/verification step (e.g., via governance or a separate namespace-registration transaction) before a `name`/`abbr` can be bound to a new `AssetIssueContract`, independent of the purely numeric `id` uniqueness already enforced by the counter.

### Proof of Concept
1. Attacker account `A` (any funded account, not the legitimate issuer) broadcasts an `AssetIssueContract` transaction with `name = "USDT"` (or any recognizable brand), `abbr` set similarly, and valid supply/time parameters.
2. With `AllowSameTokenName == 1` (current canonical mode), `doValidate()` passes because the duplicate-name check at lines 210–214 is skipped, and no ownership/allowlist check exists elsewhere.
3. `execute()` increments the unguarded global `tokenIdNum` counter and persists a new `AssetIssueCapsule` under the shared AssetIssue system, associating the brand name with attacker-controlled parameters (arbitrary supply, exchange rate `trxNum`/`num`, frozen supply schedule).
4. The resulting asset is indistinguishable at the "name" level from the real token and can be marketed/listed as official, exactly as described in the reference report for the off-ramp program.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L72-76)
```java
      long tokenIdNum = dynamicStore.getTokenIdNum();
      tokenIdNum++;
      assetIssueCapsule.setId(Long.toString(tokenIdNum));
      assetIssueCapsuleV2.setId(Long.toString(tokenIdNum));
      dynamicStore.saveTokenIdNum(tokenIdNum);
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L165-174)
```java
    if (!TransactionUtil.validAssetName(assetIssueContract.getName().toByteArray())) {
      throw new ContractValidateException("Invalid assetName");
    }

    if (dynamicStore.getAllowSameTokenName() != 0) {
      String name = assetIssueContract.getName().toStringUtf8().toLowerCase(Locale.ROOT);
      if (("trx").equals(name)) {
        throw new ContractValidateException("assetName can't be trx");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L183-186)
```java
    if ((!assetIssueContract.getAbbr().isEmpty()) && !TransactionUtil
        .validAssetName(assetIssueContract.getAbbr().toByteArray())) {
      throw new ContractValidateException("Invalid abbreviation for token");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L78-79)
```java
      long id = addExact(dynamicStore.getLatestExchangeNum(), 1);
      long now = dynamicStore.getLatestBlockHeaderTimestamp();
```
