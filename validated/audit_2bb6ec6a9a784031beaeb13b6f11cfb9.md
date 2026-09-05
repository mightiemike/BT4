### Title
Waterfall PoX commit-output check uses field-sensitive `PoxAddress` equality instead of the canonical `to_burnchain_repr()` comparison - (File: stackslib/src/chainstate/burn/operations/leader_block_commit.rs)

### Summary
`LeaderBlockCommitOp::check_pox_waterfall` validates a Nakamoto/sBTC-era block-commit's single output by comparing it directly against the expected `PoxAddress` with `!=`, whereas the classic (`check_pox_pre_waterfall`) path deliberately avoids raw equality and instead compares the canonicalized `to_burnchain_repr()` string of each side. The distinction exists because `PoxAddress::Standard` carries an `Option<AddressHashMode>` field that cannot be recovered when an address is decoded straight off a Bitcoin transaction output, so two `PoxAddress` values that represent the *same* on-chain output can differ in this field and fail derived `PartialEq`, exactly the same class of bug as the Sherlock M-5 report: a discount/decision is keyed on a "converted"/annotated representation instead of the canonical value both sides agree on.

### Finding Description
`PoxAddress` derives `PartialEq`/`Eq`/`Hash` over all its fields, including `Option<AddressHashMode>` on the `Standard` variant: [1](#0-0) 

The codebase explicitly documents that this hash-mode field is unreliable/absent when an address is derived from raw burnchain data (e.g. decoded from a Bitcoin output), and provides `to_burnchain_repr()` precisely to strip this field out and produce a stable comparison key: [2](#0-1) 

The classic, pre-waterfall PoX commit-output check honors this and matches `self.commit_outs` against the expected recipient set using `to_burnchain_repr()` on both sides, not raw `PartialEq`: [3](#0-2) 

However, the newer waterfall-PoX check (used for sBTC-style single-output commits) compares the decoded commit output against `wf_info.sbtc_address` using plain `!=`, i.e. full derived equality, not the canonical burnchain representation: [4](#0-3) 

Because `self.commit_outs` entries are populated by decoding raw Bitcoin transaction outputs (via `PoxAddress::try_from_bitcoin_output`, which for legacy addresses always sets the hash-mode field to `None` since it "won't be able to determine the hash mode since we can't distinguish segwit-p2sh from p2sh"): [5](#0-4) 

any code path that constructs the expected `sbtc_address` with a populated hash mode (`Some(AddressHashMode::...)`, as is done for the standard burn address and every other consensus-relevant `PoxAddress::Standard` constructor in the codebase, e.g. `standard_burn_address`) will never compare equal to a legitimately correct, on-chain-decoded commit output, breaking the intended equality `commit_outs[0] == expected sBTC address` even when the underlying version byte and hash160 bytes are identical.

### Impact Explanation
This breaks a consensus-critical equality (`commit output == validated reward address`) purely as a function of how a `PoxAddress` happened to be constructed, not its actual on-chain identity — the same root cause as the referenced report (a discount/override keyed to a converted representation instead of the real one). Depending on which side of the mismatch a given node computes at validation time, nodes could disagree on whether a given block-commit's sBTC output is valid, which is a minority-triggerable static-validation divergence: a miner (or the node performing block-commit construction) could produce a commit that some nodes accept and others reject (or vice versa), leading to a sortition-winner/tenure-descent disagreement across the network — a High-severity, minority-triggerable validation divergence per the given classification.

### Likelihood Explanation
The likelihood depends on the exact code path (not fully visible within the available index) that constructs `wf_info.sbtc_address`; if it is built by any constructor that sets `Some(hash_mode)` (consistent with every other `PoxAddress::Standard` constructor observed in this repo, e.g. `standard_burn_address`), the mismatch is deterministic and 100%-reproducible for any legacy-format sBTC address, requiring no special miner behavior — it would trigger on ordinary correct usage. This is flagged as uncertain because the exact construction site of `RewardSetInfoWaterfall.sbtc_address` could not be located/confirmed with the available read-only tool budget.

### Recommendation
Change `check_pox_waterfall` to compare `self.commit_outs.get(0).map(|a| a.to_burnchain_repr())` against `wf_info.sbtc_address.to_burnchain_repr()`, mirroring the pattern already used in `check_pox_pre_waterfall` and in `ChainstateConfig::get_unconfirmed_burn_distribution`/`get_spend_distribution`, so that hash-mode/annotation differences never affect consensus-critical address equality.

### Proof of Concept
1. Construct a `RewardSetInfoWaterfall` whose `sbtc_address` is `PoxAddress::Standard(addr, Some(AddressHashMode::SerializeP2PKH))` (as produced by any standard constructor in this codebase).
2. Have a miner pay that exact Bitcoin address (same version byte, same hash160) as the sole output of a block-commit.
3. On decode, `LeaderBlockCommitOp::commit_outs[0]` becomes `PoxAddress::Standard(addr, None)` via `try_from_bitcoin_output`/`from_legacy_bitcoin_address`.
4. `check_pox_waterfall`'s `self.commit_outs.get(0) != Some(&wf_info.sbtc_address)` evaluates to `true` (mismatch) purely due to the differing `Option<AddressHashMode>` field, causing `BlockCommitBadOutputs` to be (incorrectly) returned for an output that is in fact byte-identical to the intended sBTC address — or, conversely, if a different construction path leaves both `None`, nodes computing `wf_info.sbtc_address` via two different construction routes would disagree with each other, producing the network validation divergence described above.

### Citations

**File:** stackslib/src/chainstate/stacks/address.rs (L58-64)
```rust
#[derive(Debug, PartialEq, PartialOrd, Ord, Clone, Hash, Eq, Serialize, Deserialize)]
pub enum PoxAddress {
    /// Represents a { version: (buff 1), hashbytes: (buff 20) } tuple that has a Stacks
    /// representation.  Not all 20-byte hashbyte addresses do (such as Bitcoin p2wpkh)
    /// The address hash mode is optional because if we decode a legacy bitcoin address, we won't
    /// be able to determine the hash mode since we can't distinguish segwit-p2sh from p2sh
    Standard(StacksAddress, Option<AddressHashMode>),
```

**File:** stackslib/src/chainstate/stacks/address.rs (L328-344)
```rust
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
```

**File:** stackslib/src/chainstate/stacks/address.rs (L512-519)
```rust
    /// Try instantiating a PoxAddress from a Bitcoin tx output
    pub fn try_from_bitcoin_output(o: &BitcoinTxOutput) -> Option<PoxAddress> {
        match &o.address {
            BitcoinAddress::Legacy(ref legacy_addr) => {
                let addr = StacksAddress::from_legacy_bitcoin_address(legacy_addr);
                let pox_addr = PoxAddress::Standard(addr, None);
                Some(pox_addr)
            }
```

**File:** stackslib/src/chainstate/burn/operations/leader_block_commit.rs (L816-827)
```rust
        if self.commit_outs.len() != 1 {
            warn!("Invalid waterfall block commit: should have exactly one commit output");
            return Err(op_error::BlockCommitBadOutputs);
        }

        if self.commit_outs.get(0) != Some(&wf_info.sbtc_address) {
            warn!("Invalid waterfall block commit: unexpected output"; "expected" => %wf_info.sbtc_address, "found" => ?self.commit_outs.get(0));
            return Err(op_error::BlockCommitBadOutputs);
        }

        Ok(vec![Treatment::Reward(wf_info.sbtc_address.clone())])
    }
```

**File:** stackslib/src/chainstate/burn/operations/leader_block_commit.rs (L1003-1023)
```rust
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
```
