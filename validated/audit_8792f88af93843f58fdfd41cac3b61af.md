### Title
Cross-contract `Stake` action can permanently lock victim's staked funds or force-unstake validators without authorization — (`runtime/runtime/src/actions.rs`)

### Summary

The `action_stake` function in nearcore's runtime applies a `Stake` action to whichever account is the receipt's `receiver_id`, with no check that the receipt's `predecessor_id` equals the `receiver_id`. Because any contract can create a receipt targeting an arbitrary account via `promise_batch_create(victim_account_id)` and append a `Stake` action via `promise_batch_action_stake`, an unprivileged attacker can submit staking proposals on behalf of any account. This directly mirrors the external bug: just as a malicious user could call `mint(recipient=victim)` to reset the victim's liquidity cooldown, a malicious NEAR contract can call `promise_batch_action_stake` targeting the victim to reset the victim's 3-epoch staking cooldown, keeping their locked balance frozen indefinitely.

### Finding Description

**Staking cooldown in NEAR.** When a validator unstakes (`stake = 0`), their `locked` balance is not returned immediately. Per the staking invariant, `locked` equals the maximum of the last three epochs' stakes. Only after three consecutive epochs with a zero proposal does `locked` drop to zero and the balance return to `amount`. If a new non-zero staking proposal is recorded for the account in any of those epochs, the countdown resets.

**The unrestricted cross-contract Stake path.** A contract can target any account:

```rust
// runtime/near-vm-runner/src/logic/logic.rs:2319-2336
pub fn promise_batch_create(&mut self, account_id_len: u64, account_id_ptr: u64) -> Result<u64> {
    ...
    let account_id = self.read_and_parse_account_id(account_id_ptr, account_id_len)?;
    // `sir` only affects gas pricing, NOT authorization
    let sir = account_id == self.context.current_account_id;
    ...
    let new_receipt_idx = self.ext.create_action_receipt(vec![], account_id)?;
    self.checked_push_promise(Promise::Receipt(new_receipt_idx))
}
``` [1](#0-0) 

Then append a Stake action with no authorization check:

```rust
// runtime/near-vm-runner/src/logic/logic.rs:3296-3321
pub fn promise_batch_action_stake(&mut self, promise_idx: u64, amount_ptr: u64, ...) -> Result<()> {
    ...
    let (receipt_idx, sir) = self.promise_idx_to_receipt_idx_with_sir(promise_idx)?;
    self.pay_action_base(ActionCosts::stake, sir)?;
    self.ext.append_action_stake(receipt_idx, amount, public_key.decode()?);
    Ok(())
}
``` [2](#0-1) 

When the receipt arrives at the victim's shard, `action_stake` executes against the victim's account with no predecessor check:

```rust
// runtime/runtime/src/actions.rs:44-95
pub(crate) fn action_stake(
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,   // = receipt.receiver_id (victim)
    stake: &StakeAction,
    ...
) -> Result<(), RuntimeError> {
    let increment = stake.stake.saturating_sub(account.locked());
    if let Some(new_balance) = account.amount().checked_sub(increment) {
        ...
        result.validator_proposals.push(ValidatorStake::new(
            account_id.clone(),
            stake.public_key.clone(),
            stake.stake,
        ));
        if stake.stake > account.locked() {
            account.set_amount(new_balance);
            account.set_locked(stake.stake);
        }
    }
    ...
}
``` [3](#0-2) 

The validator proposal is pushed for `account_id` (the victim) regardless of who sent the receipt. The epoch manager records this proposal and uses it when computing the next epoch's validator set. [4](#0-3) 

**Cooldown-reset attack (direct analog to H-01).** Suppose the victim has `locked = X` and has submitted an unstake proposal (`stake = 0`). The attacker:

1. Deploys a malicious contract.
2. Calls `promise_batch_create(victim)` → `promise_batch_action_stake(promise_idx, X, attacker_key)`.
3. Because `stake.stake (= X) <= account.locked() (= X)`, `increment = 0`; the victim's `amount` is unchanged.
4. But `result.validator_proposals.push(ValidatorStake::new(victim, attacker_key, X))` is executed unconditionally.
5. The epoch manager records a proposal for the victim with stake `X`, overriding the victim's own `stake = 0` proposal (last proposal wins per `apply_epoch_update_to_proposals`). [5](#0-4) 

6. The 3-epoch countdown resets. The attacker repeats every epoch. The victim's `locked` balance is never returned.

**Force-unstake attack.** The attacker sends `stake = 0` to a validator's account. Since `account.locked() > 0`, the `TriesToUnstake` guard does not fire. A proposal with `stake = 0` is submitted for the victim, removing them from the validator set after two epochs. [6](#0-5) 

**Force-stake attack.** If the victim has `locked = 0` and `amount >= minimum_stake`, the attacker sends `stake = minimum_stake`. The victim's `amount` decreases and `locked` increases; a validator proposal is submitted without the victim's consent.

### Impact Explanation

- **Balance manipulation / loss of funds**: A victim who has unstaked can have their `locked` balance frozen indefinitely at zero cost to the attacker (only gas). The victim's funds are permanently inaccessible.
- **Unauthorized transaction**: The attacker submits staking proposals and modifies the `locked`/`amount` split of the victim's account without holding any key on that account.
- **Contract execution flow breakage / consensus impact**: Force-unstaking validators removes them from the active set, potentially affecting block/chunk production.

All three impacts are within the HackenProof scope for nearcore under an unprivileged user attacker model.

### Likelihood Explanation

Any account can deploy a contract. The attack requires only gas (no deposit, no special privilege). The attacker can repeat the cooldown-reset every epoch (~12 hours) indefinitely. The victim has no on-chain mechanism to prevent it.

### Recommendation

Add an authorization check in `action_stake` (or in the receipt execution path before dispatching the action) that requires `receipt.predecessor_id() == receipt.receiver_id()` for Stake actions. This mirrors the implicit guarantee that exists for transactions (where the signer must own the receiver account). Alternatively, mark `Stake` as a "self-only" action in the action validation layer so that it is rejected when it arrives via a cross-contract receipt whose predecessor differs from the receiver.

### Proof of Concept

```rust
// Malicious contract (pseudocode using NEAR SDK)
pub fn grief_unstaker(&self, victim: AccountId, victim_locked: Balance, attacker_key: PublicKey) {
    // Creates a receipt targeting `victim` with a Stake action
    // stake = victim_locked → increment = 0, no balance change, but proposal IS submitted
    Promise::new(victim).stake(victim_locked, attacker_key);
}
```

**Steps:**
1. Victim (a validator with `locked = 1_000_000 NEAR`) submits `stake = 0` to begin unstaking.
2. Attacker deploys the malicious contract and calls `grief_unstaker(victim, 1_000_000 NEAR, attacker_key)` once per epoch.
3. Each call creates a receipt that executes `action_stake` on the victim's account, pushing `ValidatorStake { account_id: victim, stake: 1_000_000 NEAR }` into `validator_proposals`.
4. `apply_epoch_update_to_proposals` uses the last proposal per account; the attacker's proposal overrides the victim's `stake = 0`.
5. The victim's `locked` is never reduced to zero. Their 1,000,000 NEAR remains permanently locked.

Relevant code locations:
- `runtime/runtime/src/actions.rs:44` — `action_stake` (no predecessor check)
- `runtime/near-vm-runner/src/logic/logic.rs:2319` — `promise_batch_create` (unrestricted target)
- `runtime/near-vm-runner/src/logic/logic.rs:3296` — `promise_batch_action_stake` (no authorization)
- `chain/epoch-manager/src/validator_selection.rs:290` — `apply_epoch_update_to_proposals` (last proposal wins)

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L2319-2336)
```rust
    pub fn promise_batch_create(
        &mut self,
        account_id_len: u64,
        account_id_ptr: u64,
    ) -> Result<u64> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_batch_create".to_string(),
            }
            .into());
        }
        let account_id = self.read_and_parse_account_id(account_id_ptr, account_id_len)?;
        let sir = account_id == self.context.current_account_id;
        self.pay_gas_for_new_receipt(sir, &[])?;
        let new_receipt_idx = self.ext.create_action_receipt(vec![], account_id)?;

        self.checked_push_promise(Promise::Receipt(new_receipt_idx))
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3296-3321)
```rust
    pub fn promise_batch_action_stake(
        &mut self,
        promise_idx: u64,
        amount_ptr: u64,
        public_key_len: u64,
        public_key_ptr: u64,
    ) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_batch_action_stake".to_string(),
            }
            .into());
        }
        let amount = Balance::from_yoctonear(
            self.memory.get_u128(&mut self.result_state.gas_counter, amount_ptr)?,
        );
        let public_key = self.get_public_key(
            public_key_ptr,
            public_key_len,
            self.ext.post_quantum_keys_enabled(),
        )?;
        let (receipt_idx, sir) = self.promise_idx_to_receipt_idx_with_sir(promise_idx)?;
        self.pay_action_base(ActionCosts::stake, sir)?;
        self.ext.append_action_stake(receipt_idx, amount, public_key.decode()?);
        Ok(())
```

**File:** runtime/runtime/src/actions.rs (L44-95)
```rust
pub(crate) fn action_stake(
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    stake: &StakeAction,
    last_block_hash: &CryptoHash,
    epoch_info_provider: &dyn EpochInfoProvider,
) -> Result<(), RuntimeError> {
    let increment = stake.stake.saturating_sub(account.locked());

    if let Some(new_balance) = account.amount().checked_sub(increment) {
        if account.locked().is_zero() && stake.stake.is_zero() {
            // if the account hasn't staked, it cannot unstake
            result.result =
                Err(ActionErrorKind::TriesToUnstake { account_id: account_id.clone() }.into());
            return Ok(());
        }

        if stake.stake > Balance::ZERO {
            let minimum_stake = epoch_info_provider.minimum_stake(last_block_hash)?;
            if stake.stake < minimum_stake {
                result.result = Err(ActionErrorKind::InsufficientStake {
                    account_id: account_id.clone(),
                    stake: stake.stake,
                    minimum_stake,
                }
                .into());
                return Ok(());
            }
        }

        result.validator_proposals.push(ValidatorStake::new(
            account_id.clone(),
            stake.public_key.clone(),
            stake.stake,
        ));
        if stake.stake > account.locked() {
            // We've checked above `account.amount >= increment`
            account.set_amount(new_balance);
            account.set_locked(stake.stake);
        }
    } else {
        result.result = Err(ActionErrorKind::TriesToStake {
            account_id: account_id.clone(),
            stake: stake.stake,
            locked: account.locked(),
            balance: account.amount(),
        }
        .into());
    }
    Ok(())
}
```

**File:** runtime/runtime/src/lib.rs (L680-690)
```rust
            Action::Stake(stake) => {
                metrics::ACTION_CALLED_COUNT.stake.inc();
                action_stake(
                    account.as_mut().expect(EXPECT_ACCOUNT_EXISTS),
                    &mut result,
                    account_id,
                    stake,
                    &apply_state.prev_block_hash,
                    epoch_info_provider,
                )?;
            }
```

**File:** chain/epoch-manager/src/validator_selection.rs (L290-328)
```rust
fn apply_epoch_update_to_proposals(
    proposals: Vec<ValidatorStake>,
    prev_epoch_info: &EpochInfo,
    validator_reward: &HashMap<AccountId, Balance>,
    validator_kickout: &HashMap<AccountId, ValidatorKickoutReason>,
    stake_change: &mut BTreeMap<AccountId, Balance>,
) -> HashMap<AccountId, ValidatorStake> {
    let mut proposals_by_account = HashMap::new();
    for p in proposals {
        let account_id = p.account_id();
        if validator_kickout.contains_key(account_id) {
            let account_id = p.take_account_id();
            stake_change.insert(account_id, Balance::ZERO);
        } else if let Some(ValidatorKickoutReason::ProtocolVersionTooOld { .. }) =
            prev_epoch_info.validator_kickout().get(account_id)
        {
            // If the validator was kicked out because of an old protocol version in T-1,
            // it is not allowed back in T.
            continue;
        } else {
            stake_change.insert(account_id.clone(), p.stake());
            proposals_by_account.insert(account_id.clone(), p);
        }
    }

    for r in prev_epoch_info.validators_iter() {
        let account_id = r.account_id().clone();
        if validator_kickout.contains_key(&account_id) {
            stake_change.insert(account_id, Balance::ZERO);
            continue;
        }
        let p = proposals_by_account.entry(account_id).or_insert(r);
        if let Some(reward) = validator_reward.get(p.account_id()) {
            *p.stake_mut() = p.stake().checked_add(*reward).unwrap();
        }
        stake_change.insert(p.account_id().clone(), p.stake());
    }

    proposals_by_account
```
