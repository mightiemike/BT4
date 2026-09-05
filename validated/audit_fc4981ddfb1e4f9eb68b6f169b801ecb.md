## Title
User-Chosen PoX Address Colliding with the Reserved "Burn Address" Sentinel Breaks Reward/Punishment Accounting - ([File: stackslib/src/chainstate/stacks/address.rs])

### Summary
`PoxAddress::standard_burn_address()` returns a `Standard` address built from `StacksAddress::burn_address(mainnet)` — a legacy P2PKH address whose `Hash160` is all zero bytes. This exact value is used as a consensus-critical sentinel to represent (a) miner PoB (burn) outputs and (b) back-filled/empty reward-set slots. `PoxAddress::is_burn()` treats any `Standard` address with this all-zero hash as "burn," with no distinction between the sentinel and a real user address that happens to have the same bytes. [1](#0-0) 

Because a stacker's PoX reward address is a raw, attacker-supplied `{version, hashbytes}` buffer whose only validation is a length/version check (`check-pox-addr-hashbytes`/`check-pox-addr-version`), nothing prevents a user from stacking with `hashbytes = 0x00…00`, producing a `PoxAddress` that is bit-for-bit identical to the reserved burn sentinel. [2](#0-1) 

### Finding Description
This is the same bug class as the reported "missing zero-value check" (a security-relevant value that is trusted to be non-zero/non-sentinel is never validated as such): the codebase never checks that a real stacker's chosen reward address is *not* the reserved zero/burn sentinel before it is folded into consensus-critical structures.

The zero-hash address is deliberately overloaded to mean two different things:
1. A miner's burn output (no reward paid) — used e.g. in `RewardSetInfo::into_commit_outs` when padding a reward set with `PoxAddress::standard_burn_address()`. [3](#0-2) 
2. A "no recipient" placeholder for a reward-cycle slot that was never Stacked to.

Downstream consensus logic (`leader_block_commit.rs`, `check_pox_lockup`, treatment/punish logic) branches explicitly on `addr.is_burn()`:
- `recipient_set_all_burns` short-circuits to "no PoX descendant check required." [4](#0-3) 
- A commit output matching a burn address is treated specially in the punishment path (`allow_nakamoto_punishment`), letting a burn output stand in for either a real punished recipient or an actual burn. [5](#0-4) 
- `calculate_paid_rewards` explicitly special-cases `addr.is_burn()` to divert that output's fee into `burn_amt` instead of crediting `reward_recipients`. [6](#0-5) 

If a legitimate stacker sets `hashbytes` to all zeros for their reward PoX address (only length/version are validated, not the value), the resulting `PoxAddress` is indistinguishable, by `is_burn()`/`to_burnchain_repr()`, from the sentinel burn address used elsewhere for padding and for "no recipient." This creates a real ambiguity in the equality this system depends on: *"the reward-set entry that a stacker locked funds for" == "the address a miner must pay to be rewarded."* A miner's block-commit that pays that slot as a burn output (because it matches `is_burn()`) is treated by the protocol identically to a real burn, silently diverting the stacker's expected reward into `burn_amt` rather than `reward_recipients` — even though a genuine stacker locked STX for that slot.

### Impact Explanation
This is bounded to reward mis-payment (a stacker who deliberately or accidentally chooses the zero-hash PoX address never receives PoX payouts to that slot; miners can legitimately just burn to that output and it is accounted as a "burn" rather than a "reward"). It does not require a majority of any set — a single unprivileged stacker can create this condition simply by supplying `hashbytes = 0`. It does not by itself cause a state root mismatch or split, since both classic-PoX comparisons (`to_burnchain_repr()`) and payout accounting are deterministic and every node computes the same "is_burn" classification — so this is not a chain-split risk. It is best characterized as a reward mis-payment/loss bounded to the amount owed to that reward slot, i.e., a High-adjacent (but likely Low/Medium in practice) reward-loss issue rather than a critical consensus divergence, since all nodes agree on the (wrong) outcome.

### Likelihood Explanation
Low-to-moderate likelihood of accidental triggering (a stacking pool operator or buggy signer/tooling could supply an all-zero PoX address by mistake), and trivial for any single stacker to trigger deliberately (no coordination, no privileged access, no majority needed) since the value is user-supplied and only length/version-checked, never checked against the burn sentinel.

### Recommendation
Add an explicit "not-the-burn-address" check alongside the existing `check-pox-addr-version`/`check-pox-addr-hashbytes` validation in the PoX contracts (`pox-2.clar`, `pox-3.clar`, `pox-4.clar`, and any successor) and/or in `PoxAddress::try_from_pox_tuple`/`minimal-can-stack-stx`, rejecting a stacker-supplied PoX address whose bytes equal `StacksAddress::burn_address(mainnet/testnet)`. This closes the ambiguity between "no recipient" sentinel and a legitimately-Stacked address, mirroring the recommended fix in the analog report (validate that a security-critical address parameter cannot silently collide with a reserved zero value).

### Proof of Concept
1. Call `stack-stx` (or `delegate-stack-stx`) on `pox-4.clar` with `pox-addr = { version: 0x00, hashbytes: 0x0000000000000000000000000000000000000000 }` (20 zero bytes). `check-pox-addr-version` passes (version 0 ≤ `MAX_ADDRESS_VERSION`), and `check-pox-addr-hashbytes` only checks length (20 bytes for version 0), so the call succeeds. [2](#0-1) 
2. This reward-cycle slot is now populated with a `PoxAddress::Standard(StacksAddress::burn_address(mainnet), ...)`-equivalent value — identical to what `standard_burn_address()` returns and to what `is_burn()` reports as `true`. [1](#0-0) 
3. When a miner mines the block for that reward cycle and pays the corresponding output as a burn (satisfying `is_burn()`), `calculate_paid_rewards` classifies the paid amount as `burn_amt` rather than crediting the stacker's slot in `reward_recipients`, and the leader-block-commit treatment logic (`recipient_set_all_burns` / null-miner-punishment matching) treats the slot as fungible with an actual burn output. [6](#0-5) [5](#0-4) 
4. The stacker who locked STX for that slot never receives BTC/sBTC value distinguishable from a "no one stacked here" burn, resulting in reward loss/misattribution for that slot, even though the chain state remains internally consistent across all nodes (no fork/consensus divergence, since the classification is deterministic).

**Note on verification limits:** I was not able to fully trace `StacksAddress::is_burn()` / `StacksAddress::burn_address()` implementations (only import references were found in the index, not their bodies) due to index size limits, so the exact byte-for-byte equivalence assertion (zero hash160 == burn sentinel) is inferred from the doc comment on `standard_burn_address()` ("Make a standard burn address, i.e. as a legacy p2pkh address comprised of all 0's… this behavior… is *consensus critical*") rather than from reading the `is_burn`/`burn_address` bodies directly. A Devin session with full repo access could confirm this directly by inspecting `stacks-common/src/types/chainstate.rs` (or wherever `StacksAddress` is defined) for `is_burn`/`burn_address`.

### Citations

**File:** stackslib/src/chainstate/stacks/address.rs (L320-355)
```rust
    /// Is this a burn address?
    pub fn is_burn(&self) -> bool {
        match *self {
            PoxAddress::Standard(ref addr, _) => addr.is_burn(),
            _ => false,
        }
    }

    /// What is the burnchain representation of this address?
    /// Used for comparing addresses from block-commits, where certain information (e.g. the hash
    /// mode) can't be used since it's not stored there.  The resulting string encodes all of the
    /// information that is present on the burnchain, and it does so in a _stable_ way.
    pub fn to_burnchain_repr(&self) -> String {
        match *self {
            PoxAddress::Standard(ref addr, _) => {
                format!("{:02x}-{}", &addr.version(), &addr.bytes())
            }
            PoxAddress::Addr20(_, ref addrtype, ref addrbytes) => {
                format!("{:02x}-{}", addrtype.to_u8(), to_hex(addrbytes))
            }
            PoxAddress::Addr32(_, ref addrtype, ref addrbytes) => {
                format!("{:02x}-{}", addrtype.to_u8(), to_hex(addrbytes))
            }
        }
    }

    /// Make a standard burn address, i.e. as a legacy p2pkh address comprised of all 0's.
    /// NOTE: this is used to represent both PoB outputs, as well as to back-fill reward set data
    /// when storing a reward cycle's sortition for which there are no output slots.  This means
    /// that the behavior of this method is *consensus critical*
    pub fn standard_burn_address(mainnet: bool) -> PoxAddress {
        PoxAddress::Standard(
            StacksAddress::burn_address(mainnet),
            Some(AddressHashMode::SerializeP2PKH),
        )
    }
```

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L473-483)
```text
;; Is the address mode valid for a PoX address?
(define-read-only (check-pox-addr-version (version (buff 1)))
    (<= (buff-to-uint-be version) MAX_ADDRESS_VERSION))

;; Is this buffer the right length for the given PoX address?
(define-read-only (check-pox-addr-hashbytes (version (buff 1)) (hashbytes (buff 32)))
    (if (<= (buff-to-uint-be version) MAX_ADDRESS_VERSION_BUFF_20)
        (is-eq (len hashbytes) u20)
        (if (<= (buff-to-uint-be version) MAX_ADDRESS_VERSION_BUFF_32)
            (is-eq (len hashbytes) u32)
            false)))
```

**File:** stackslib/src/chainstate/burn/operations/leader_block_commit.rs (L728-748)
```rust
    pub fn into_commit_outs(from: Option<RewardSetInfo>, mainnet: bool) -> Vec<PoxAddress> {
        match from {
            Some(RewardSetInfo::V0(v0)) => {
                let mut outs: Vec<_> = v0
                    .recipients
                    .into_iter()
                    .map(|(recipient, _)| recipient)
                    .collect();
                while outs.len() < OUTPUTS_PER_COMMIT {
                    outs.push(PoxAddress::standard_burn_address(mainnet));
                }
                outs
            }
            Some(RewardSetInfo::Waterfall(wf)) => {
                vec![wf.sbtc_address]
            }
            None => (0..OUTPUTS_PER_COMMIT)
                .map(|_| PoxAddress::standard_burn_address(mainnet))
                .collect(),
        }
    }
```

**File:** stackslib/src/chainstate/burn/operations/leader_block_commit.rs (L894-923)
```rust
        if burnchain.is_in_prepare_phase(self.block_height) {
            if let Err(e) = self.check_prepare_commit_burn() {
                warn!("Invalid block commit: in block {} which is in the prepare phase, but did not burn to a single output as expected ({:?})", self.block_height, &e);
                return Err(op_error::BlockCommitBadOutputs);
            }
            return Ok(vec![]);
        }

        // Not in prepare phase, so this can be either PoB or PoX (a descent check from the
        // anchor block will be necessary if the block-commit is well-formed).
        //
        // first, handle a corner case:
        //    all of the commitment outputs are _burns_
        //    _and_ the reward set chose two burn addresses as reward addresses.
        // then, don't need to do a pox descendant check.
        let recipient_set_all_burns = v0
            .recipients
            .iter()
            .fold(true, |prior_is_burn, (addr, ..)| {
                prior_is_burn && addr.is_burn()
            });

        if recipient_set_all_burns {
            if !self.all_outputs_burn() {
                warn!("Invalid block commit: recipient set should be all burns");
                return Err(op_error::BlockCommitBadOutputs);
            }
            return Ok(vec![]);
        }

```

**File:** stackslib/src/chainstate/burn/operations/leader_block_commit.rs (L950-1041)
```rust
        if self.all_outputs_burn() {
            // If we're not descended from the anchor, then great, this is just a "normal" non-descendant burn commit
            // But, if we are descended from the anchor and nakamoto pox punishments are allowed, this commit may have
            //  been a double punishment
            if !descended_from_anchor {
                return Ok(vec![]);
            }
            if v0.allow_nakamoto_punishment {
                // all non-burn recipients were punished -- when we do the block processing
                //  enforcement check, "burn recipients" can be treated as 1 or a 0 in the
                //  bitvec interchangeably (whether they are punished or not doesn't matter).
                let punished = v0
                    .recipients
                    .iter()
                    .map(|(addr, _)| Treatment::Punish(addr.clone()))
                    .collect();
                return Ok(punished);
            } else {
                warn!(
                    "Invalid block commit: descended from PoX anchor {}, but used burn outputs",
                    &v0.anchor_block
                );
                return Err(op_error::BlockCommitBadOutputs);
            }
        } else {
            let mut check_recipients: Vec<_> = v0
                .recipients
                .iter()
                .map(|(addr, ix)| (addr.clone(), *ix))
                .collect();

            if check_recipients.len() == 1 {
                // If the number of recipients in the set was odd, we need to pad
                // with a burn address.
                // NOTE: this used the old burnchain.is_mainnet() code, which always
                // returns false
                check_recipients.push((PoxAddress::standard_burn_address(false), 0))
            }

            if self.commit_outs.len() != check_recipients.len() {
                warn!(
                    "Invalid block commit: expected {} PoX transfers, but commit has {}",
                    v0.recipients.len(),
                    self.commit_outs.len()
                );
                return Err(op_error::BlockCommitBadOutputs);
            }

            // we've checked length equality, so we can just iterate through
            //  self.commit_outs and check if each is in `check_recipients`
            //  *OR* if `allows_pox_punishment`, then it could be a burn.
            // NOTE: we do a find and remove here so that the same recipient
            //  isn't found multiple times by different commit_outs.
            let mut rewarded = vec![];
            for self_commit in self.commit_outs.iter() {
                let search_predicate = self_commit.to_burnchain_repr();
                let found = check_recipients
                    .iter()
                    .enumerate()
                    .find(|(_, (check_commit, _))| {
                        search_predicate == check_commit.to_burnchain_repr()
                    });
                if let Some((index, _)) = found {
                    rewarded.push(Treatment::Reward(check_recipients.remove(index).0));
                } else {
                    // if we didn't find the pox output, then maybe its a pox punishment?
                    if v0.allow_nakamoto_punishment && self_commit.is_burn() {
                        continue;
                    } else {
                        warn!("Invalid block commit: committed output {} does not match expected recipient set: {:?}",
                              self_commit.to_burnchain_repr(), check_recipients);
                        return Err(op_error::BlockCommitBadOutputs);
                    }
                };
            }

            if !descended_from_anchor {
                warn!(
                    "Invalid block commit: not descended from PoX anchor {}, but used PoX outputs",
                    &v0.anchor_block
                );
                return Err(op_error::BlockCommitBadOutputs);
            }

            let mut treated_outputs: Vec<_> = check_recipients
                .into_iter()
                .map(|x| Treatment::Punish(x.0))
                .collect();
            treated_outputs.extend(rewarded);
            return Ok(treated_outputs);
        }
    }
```

**File:** stackslib/src/chainstate/coordinator/mod.rs (L876-916)
```rust
pub fn calculate_paid_rewards(ops: &[BlockstackOperationType]) -> PaidRewards {
    let mut reward_recipients: HashMap<_, u64> = HashMap::new();
    let mut burn_amt = 0;
    let mut pox_transactions = Vec::new();
    for op in ops.iter() {
        if let BlockstackOperationType::LeaderBlockCommit(commit) = op {
            if commit.commit_outs.is_empty() {
                continue;
            }
            let amt_per_address = commit.burn_fee / (commit.commit_outs.len() as u64);
            let mut tx_reward_recipients = Vec::new();
            for (utxo_idx, addr) in commit.commit_outs.iter().enumerate() {
                if addr.is_burn() {
                    burn_amt += amt_per_address;
                    continue;
                }
                if let Some(prior_amt) = reward_recipients.get_mut(addr) {
                    *prior_amt += amt_per_address;
                } else {
                    reward_recipients.insert(addr.clone(), amt_per_address);
                }
                tx_reward_recipients.push(PoxTransactionRewardRecipient {
                    recipient: addr.clone(),
                    amt: amt_per_address,
                    utxo_idx: utxo_idx as u32,
                });
            }
            if !tx_reward_recipients.is_empty() {
                pox_transactions.push(PoxTransactionReward {
                    txid: commit.txid.clone(),
                    reward_recipients: tx_reward_recipients,
                });
            }
        }
    }
    PaidRewards {
        pox: reward_recipients.into_iter().collect(),
        burns: burn_amt,
        pox_transactions,
    }
}
```
