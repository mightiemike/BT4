[File: 'File Name: types/src/ledger_info.rs -> Scope: Critical. Unprivileged input can cause committed state to differ from the VM output that honest validators should derive.'] [Symbol: SignatureAggregator::check_voting_power vs aggregate_and_verify ordering] Can an attacker submit enough syntactically valid but cryptographically invalid SignatureWithStatus entries to reach check_voting_power's optimistic 'all_voters' count and cause a caller that gates further

### Citations

**File:** types/src/ledger_info.rs (L53-61)
```rust
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, CryptoHasher, BCSCryptoHash)]
#[cfg_attr(any(test, feature =
