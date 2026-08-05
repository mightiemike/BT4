### Title
Lack of On-Chain Token/Asset Name Normalization Allows Case-Variant Duplicate Registrations Enabling Impersonation - (File: actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java)

### Summary
`AssetIssueActuator.validate()` fails to normalize the case of a proposed asset (token) name before checking uniqueness, allowing an unprivileged user to register a token whose name is visually identical to an existing token but differs only in letter casing (e.g. `USDT` vs `USDt`). This is the same bug class as the ZNS report: names that a client/off-chain consumer would treat as equivalent are stored/hashed as distinct on-chain entities because the contract logic performs no lowercase normalization prior to the uniqueness check.

### Finding Description
`AssetIssueActuator.validate()` only checks the reserved word `"trx"` case-insensitively: [1](#0-0) 

However, the actual uniqueness enforcement for the token name — the part that is supposed to guarantee that a name maps to exactly one asset — is done via a raw byte-exact lookup with no normalization at all: [2](#0-1) 

`TransactionUtil.validAssetName` / `validReadableBytes` only restrict the name to printable ASCII characters (`0x21`–`0x7E`); it places no constraint on case and performs no normalization: [3](#0-2) 

As a result, when `AllowSameTokenName == 0` (the mode where the protocol is explicitly meant to enforce "one name → one token"), an attacker can still register `"Bitcoin"`, `"BITCOIN"`, and `"BitCoin"` as three separate, independently-owned `AssetIssueContract` entries, since `assetIssueStore.get(name.toByteArray())` treats each casing as a distinct key. This directly mirrors the ZNS root cause: the contract logic assumes off-chain/client-side normalization (case-insensitive display) will make these look the same to users, but on-chain they are entirely separate, differently-owned assets/hashes.

This is different from `AccountUpdateContract`'s account name, which the protocol explicitly documents as *not* intended to be unique (`account_contract.proto` comment: "Account name is not unique now"), and different from `SetAccountIdContract`'s `account_id`, which the protocol *does* correctly document and implement as case-insensitive via `AccountIdIndexStore.getLowerCaseAccountId()`: [4](#0-3) 

The asset-name path is the one place where uniqueness is explicitly the intended guarantee (enforced via the `"Token exists"` error) but the implementation is inconsistent — case is normalized only for the reserved `"trx"` check, not for general duplicate detection.

### Impact Explanation
Any unprivileged account can call `AssetIssueContract`/`AssetIssueActuator` to issue a token. By exploiting the case-sensitive uniqueness check, an attacker can register a token whose name is a case-variant of a legitimate, already-issued token (e.g. impersonating a well-known token like `USDT`/`usdt`/`Usdt`). Wallets, exchanges, or explorers that display token names to end users (and which may not visually distinguish casing, or which a careless user may not notice) can be tricked into treating the attacker's asset as the legitimate one, leading to users sending TRX to participate in (`ParticipateAssetIssueContract`) or otherwise interacting with a spoofed/duplicate asset. This is an accounting/impersonation risk consistent with the "domain spoofing" bug class in the report, though the severity is bounded by the fact that TRON's `AssetIssueContract` model has largely been superseded by TRC-10/TRC-20 tooling and most modern wallets resolve tokens by contract address rather than by name lookup alone.

### Likelihood Explanation
Likelihood is moderate: the underlying validation gap is trivially reachable by any account with sufficient balance to pay the `AssetIssueFee`, requires no special permission, and the vulnerable code path (`AssetIssueActuator.validate()`) is on the default transaction-processing path. The main mitigating factor is that exploitation for actual impersonation requires end users or downstream services (wallets/exchanges) to fail to distinguish casing when displaying/resolving asset names — a client-side condition outside java-tron's control, exactly as the original ZNS report also concluded ("gas/complexity trade-off" mitigations were suggested rather than a hard requirement).

### Recommendation
Normalize the asset name (and abbreviation) to a canonical case (e.g. lowercase) before performing the uniqueness check in `AssetIssueActuator.validate()`, consistent with how `AccountIdIndexStore` already normalizes `account_id`. Concretely, in `AssetIssueActuator.validate()`, replace the raw-byte lookup at lines 210–214 with a lookup keyed on `name.toLowerCase(Locale.ROOT)` (as is already done for the `"trx"` reserved-word check), and consider rejecting case-only-conflicting registrations with the same `"Token exists"` error even when `AllowSameTokenName` is set to 0.

### Proof of Concept
1. Attacker (or user 1) issues `AssetIssueContract` with `name = "Bitcoin"` while `AllowSameTokenName == 0`. `AssetIssueActuator.validate()` passes; `assetIssueStore.get("Bitcoin".getBytes())` is null, so `execute()` stores the asset.
2. Attacker issues a second `AssetIssueContract` with `name = "BITCOIN"` (different owner address). `AssetIssueActuator.validate()` at lines 210–214 performs `assetIssueStore.get("BITCOIN".getBytes())`, which is a distinct byte-array key from `"Bitcoin"`, so the lookup returns null and the `"Token exists"` exception is never thrown.
3. Both `"Bitcoin"` and `"BITCOIN"` now exist on-chain as separate, independently owned assets, each satisfying the protocol's "one name → one token" invariant individually, but visually indistinguishable to end users relying on case-insensitive display — enabling impersonation of the original asset. [2](#0-1)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java (L169-174)
```java
    if (dynamicStore.getAllowSameTokenName() != 0) {
      String name = assetIssueContract.getName().toStringUtf8().toLowerCase(Locale.ROOT);
      if (("trx").equals(name)) {
        throw new ContractValidateException("assetName can't be trx");
      }
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

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L89-118)
```java
  public static boolean validAssetName(byte[] assetName) {
    return validReadableBytes(assetName, MAX_ASSET_NAME_LEN);
  }

  public static boolean validTokenAbbrName(byte[] abbrName) {
    return validReadableBytes(abbrName, MAX_TOKEN_ABBR_NAME_LEN);
  }

  private static boolean validBytes(byte[] bytes, int maxLength, boolean allowEmpty) {
    if (ArrayUtils.isEmpty(bytes)) {
      return allowEmpty;
    }
    return bytes.length <= maxLength;
  }

  private static boolean validReadableBytes(byte[] bytes, int maxLength) {
    if (ArrayUtils.isEmpty(bytes) || bytes.length > maxLength) {
      return false;
    }
    // b must be readable
    for (byte b : bytes) {
      if (b < 0x21) {
        return false; // 0x21 = '!'
      }
      if (b > 0x7E) {
        return false; // 0x7E = '~'
      }
    }
    return true;
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L23-31)
```java
  private static byte[] getLowerCaseAccountId(byte[] bsAccountId) {
    return ByteString
        .copyFromUtf8(ByteString.copyFrom(bsAccountId).toStringUtf8().toLowerCase(Locale.ROOT))
        .toByteArray();
  }

  public void put(AccountCapsule accountCapsule) {
    byte[] lowerCaseAccountId = getLowerCaseAccountId(accountCapsule.getAccountId().toByteArray());
    super.put(lowerCaseAccountId, new BytesCapsule(accountCapsule.getAddress().toByteArray()));
```
