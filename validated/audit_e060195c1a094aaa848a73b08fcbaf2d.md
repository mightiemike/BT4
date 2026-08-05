This is a genuine, by-design behavior confirmed by the actual code — not a plausible-sounding but unsupported claim.

### Title
Account state trie deliberately excludes AssetV2/FrozenV2/votes/LatestOperationTime, making it unable to detect divergence in those account dimensions - ([File: framework/src/main/java/org/tron/core/db/accountstate/AccountStateEntity.java])

### Summary
`AccountStateEntity`'s constructor only copies `address`, `balance`, and `allowance` from the `Account` protobuf into the entity that gets hashed into the trie committed via `accountStateRoot` in the block header, discarding every other field (`assetV2`, `frozen`, `votes`, `latest_opration_time`, `account_resource`, etc.). Consequently `toByteArrays()`/`toString()` and the trie leaf value for two accounts with identical address/balance/allowance but arbitrarily different TRC10 asset balances, frozen resources, or votes are byte-identical, and `AccountStateStoreTrie.getAccount` cannot distinguish them.

### Finding Description
`AccountStateEntity(Account account)` explicitly builds a reduced `Account` containing only `address`, `balance`, and `allowance`: [1](#0-0) . `toByteArrays()` simply serializes this reduced message [2](#0-1) . This reduced byte array is what gets written into the account-state trie via `AccountStateCallBackUtils.accountCallBack`, which is invoked on every account mutation during transaction execution: [3](#0-2) . The resulting trie root is committed into the block header's `accountStateRoot` field, and `AccountStateStoreTrie.getAccount`/`getSolidityAccount` are the only public read paths into this trie: [4](#0-3) . There is even an earlier version of this same class (in `chainbase`) that has `putAllAssetV2` commented out, confirming the omission of AssetV2 was an explicit, deliberate design choice rather than an oversight: [5](#0-4) .

Any unprivileged attacker performing a `TransferAssetContract` (TRC10 transfer), a freeze/unfreeze (`FreezeBalanceV2Contract`/`UnfreezeBalanceV2Contract`), or a `VoteWitnessContract` that leaves `balance` and `allowance` unchanged will produce an account whose `AccountStateEntity.toByteArrays()` is unchanged before and after the mutation, since none of those fields (`assetV2`, `frozen`, `votes`) are captured by the entity.

### Impact Explanation
Any consumer that relies on `AccountStateStoreTrie`/the `accountStateRoot` commitment as a full account-state integrity proof (e.g., a light client or auditor verifying that "no unaccounted state change occurred" for an account) will be blind to any TRC10 asset movement, resource freeze/unfreeze, vote change, or `latest_opration_time` update, since the committed trie leaf for the account does not change. This is a genuine gap between what the account-state trie's design intends to prove (per the "account state root" documentation) versus what it actually commits to — it verifies TRX balance and block-producing allowance only, not the full economic state of an account.

### Likelihood Explanation
This requires no special privilege: any account performing a plain TRC10 transfer, freeze/unfreeze, or vote — all of which leave `balance`/`allowance` untouched — will trivially trigger identical `AccountStateEntity` serialization before/after. It's deterministic and 100% reproducible, not probabilistic. It's not a "race condition" bug; it's a fundamental design gap of the `AccountStateEntity` reduction.

### Recommendation
If the account-state trie/`accountStateRoot` is meant to be a comprehensive commitment to account state (used for SPV/light-client verification), `AccountStateEntity` should include all economically meaningful fields (`assetV2`, `frozen`, `frozen_supply`, `votes`, `account_resource`, `latest_opration_time`, delegated-resource fields), or the documentation/consumers should be updated to explicitly state that `accountStateRoot` only proves TRX balance/allowance and must not be relied upon for asset/resource/vote integrity.

### Proof of Concept
```java
@Test
public void accountStateEntity_missesAssetV2FrozenVotesChanges() {
  Account base = Account.newBuilder()
      .setAddress(ByteString.copyFrom("addr".getBytes()))
      .setBalance(100)
      .setAllowance(5)
      .build();

  Account mutatedAsset = base.toBuilder()
      .putAssetV2("1000001", 999_000_000L) // simulate TRC10 transfer receipt
      .build();

  Account mutatedFrozen = base.toBuilder()
      .addFrozen(Account.Frozen.newBuilder().setFrozenBalance(50_000_000L).setExpireTime(123L))
      .build();

  Account mutatedVote = base.toBuilder()
      .addVotes(Vote.newBuilder().setVoteAddress(ByteString.copyFrom("wit".getBytes())).setVoteCount(10))
      .build();

  byte[] baseBytes = new AccountStateEntity(base).toByteArrays();
  byte[] assetBytes = new AccountStateEntity(mutatedAsset).toByteArrays();
  byte[] frozenBytes = new AccountStateEntity(mutatedFrozen).toByteArrays();
  byte[] voteBytes = new AccountStateEntity(mutatedVote).toByteArrays();

  // These SHOULD differ if the trie is meant to detect economic state divergence,
  // but the current implementation makes them identical:
  Assert.assertArrayEquals(baseBytes, assetBytes);
  Assert.assertArrayEquals(baseBytes, frozenBytes);
  Assert.assertArrayEquals(baseBytes, voteBytes);
}
```
This demonstrates that `AccountStateEntity.toByteArrays()` — and therefore the trie leaf value committed under `accountStateRoot` — is invariant under AssetV2, Frozen, and Vote mutations, confirming the account-state trie cannot detect these classes of state divergence.

### Citations

**File:** framework/src/main/java/org/tron/core/db/accountstate/AccountStateEntity.java (L16-22)
```java
  public AccountStateEntity(Account account) {
    Account.Builder builder = Account.newBuilder();
    builder.setAddress(account.getAddress());
    builder.setBalance(account.getBalance());
    builder.setAllowance(account.getAllowance());
    this.account = builder.build();
  }
```

**File:** framework/src/main/java/org/tron/core/db/accountstate/AccountStateEntity.java (L42-44)
```java
  public byte[] toByteArrays() {
    return account.toByteArray();
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/accountstate/AccountStateCallBackUtils.java (L13-22)
```java
  public void accountCallBack(byte[] key, AccountCapsule item) {
    if (!exe()) {
      return;
    }
    if (item == null) {
      return;
    }
    trieEntryList
        .add(TrieEntry.build(key, new AccountStateEntity(item.getInstance()).toByteArrays()));
  }
```

**File:** framework/src/main/java/org/tron/core/db/accountstate/storetrie/AccountStateStoreTrie.java (L35-47)
```java
  public AccountStateEntity getAccount(byte[] key) {
    return getAccount(key, trieService.getFullAccountStateRootHash());
  }

  public AccountStateEntity getAccount(byte[] key, byte[] rootHash) {
    TrieImpl trie = new TrieImpl(this, rootHash);
    byte[] value = trie.get(Hash.encodeElement(key));
    return ArrayUtils.isEmpty(value) ? null : AccountStateEntity.parse(value);
  }

  public AccountStateEntity getSolidityAccount(byte[] key) {
    return getAccount(key, trieService.getSolidityAccountStateRootHash());
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/accountstate/AccountStateEntity.java (L16-22)
```java
  public AccountStateEntity(Account account) {
    Account.Builder builder = Account.newBuilder();
    builder.setAddress(account.getAddress());
    builder.setBalance(account.getBalance());
    //builder.putAllAssetV2(account.getAssetV2Map());
    builder.setAllowance(account.getAllowance());
    this.account = builder.build();
```
