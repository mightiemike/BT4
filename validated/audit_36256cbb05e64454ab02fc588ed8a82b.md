### Title
Unconditional `EthAddress` conversion panics the sequencer when an L3 contract sends an L2-to-L1 message with an out-of-range `to_address` - (File: `crates/blockifier/src/execution/call_info.rs`)

### Summary
The `send_message_to_l1` syscall only validates that `to_address` fits in an `EthAddress` (160 bits) when the chain is **not** an L3 (`is_l3 == false`). On L3 chains this check is deliberately skipped, allowing `to_address` to be an arbitrary felt/`L1Address`. However, when the call info is later summarized/ordered into a canonical message list, the code unconditionally performs `self.message.to_address.try_into().expect("Failed to convert L1Address to EthAddress")`, with no corresponding "only convert when needed" gating. This mirrors the reported bug class exactly: a downstream stage performs an eager, unconditional fallible conversion that the entry-point logic had intentionally made conditional, leading to a crash (`panic`) instead of a graceful error/skip.

### Finding Description
`syscall_base.rs::send_message_to_l1` gates the `EthAddress` range check on `is_l3`: [1](#0-0) 

```rust
pub fn send_message_to_l1(&mut self, message: MessageToL1) -> SyscallResult<()> {
    if !self.context.tx_context.block_context.chain_info.is_l3 {
        EthAddress::try_from(message.to_address)?;
    }
    ...
}
```

This is confirmed by the dedicated test `test_send_message_to_l1_invalid_address`, which explicitly asserts that on `is_l3 == true` the call **succeeds** even with an out-of-range address (`0x10000000000000000000000000000000000000000`, far larger than 160 bits): [2](#0-1) 

The message is then stored as an `OrderedL2ToL1Message` carrying an `L1Address` (an unconstrained `Felt` wrapper, not validated to be `EthAddress`-sized): [3](#0-2) 

Later, in `OrderedItem::to_ordered_tuple`, which converts the internal ordered message representation to the externally consumed `starknet_api::transaction::MessageToL1`, the code performs an unconditional, panicking conversion: [4](#0-3) 

```rust
impl OrderedItem for OrderedL2ToL1Message {
    type UnorderedItem = StarknetAPIMessageToL1;
    fn to_ordered_tuple(&self, from_address: ContractAddress) -> (usize, Self::UnorderedItem) {
        (
            self.order,
            StarknetAPIMessageToL1 {
                from_address,
                to_address: self
                    .message
                    .to_address
                    .try_into()
                    .expect("Failed to convert L1Address to EthAddress"),
                payload: self.message.payload.clone(),
            },
        )
    }
    ...
}
```

This exactly matches the reported bug class: the "front door" check (`send_message_to_l1`) was made conditional (skip check on L3), but a downstream consumer of the data (`to_ordered_tuple`, used when building call info / receipts / commitments for the block) still unconditionally assumes the earlier check always ran, and uses `.expect()` rather than proper error handling or a conditional-only conversion. Unlike the reference `ExpectedWithdrawals` bug (which returns a `Result::Err`), this variant is worse: it calls `.expect()`, which **panics the process** rather than merely propagating an error.

### Impact Explanation
On any chain configured as L3 (`chain_info.is_l3 == true`), an unprivileged account can submit an ordinary `invoke` transaction whose Cairo code calls the `send_message_to_l1` syscall with a `to_address` felt greater than `2^160 - 1`. The syscall succeeds (per the confirmed test behavior), the message is retained in the call info, and once that call info is summarized (e.g., for building the transaction receipt / a block's outputs), the `.expect()` in `to_ordered_tuple` panics. A panic in this execution path during block building can crash/abort the sequencer process handling that block, causing denial of service and preventing new transactions from being confirmed — matching the "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
High reachability: this requires only a normal `invoke` transaction from any unprivileged sender to a contract they control (or an existing contract) that calls `send_message_to_l1` with an out-of-range address — no special privileges, no reliance on malicious operators/provers. The precondition is that the deployment is configured with `chain_info.is_l3 = true` (an increasingly common deployment mode for Starknet L3 appchains). Given the existing dedicated unit test explicitly exercises and asserts the "no validation on L3" behavior, the split-validation logic is confirmed to exist in production code, not merely hypothetical.

### Recommendation
Do not defer to an unconditional `.expect()`/`try_into()` in `to_ordered_tuple`. Instead, mirror the syscall's `is_l3` conditional logic: either (a) always validate/clamp `to_address` at syscall time regardless of `is_l3` (storing a canonical, possibly zero-padded, representation) so downstream consumers can rely on the invariant, or (b) make `to_ordered_tuple` (and any `MessageToL1`-consuming code, e.g., receipt construction, block hash/commitment output serialization) return a proper `Result` and propagate a recoverable transaction-level error/revert instead of panicking, consistent with how the analogous `is_l3`-check works. In particular, `StarknetAPIMessageToL1::to_address` typing should be reconsidered for the L3 case, since `EthAddress` structurally cannot represent felts used as generic L3 message targets.

### Proof of Concept
1. Deploy/configure a sequencer node with `chain_info.is_l3 = true` (as used identically in `test_send_message_to_l1_invalid_address`).
2. Deploy a simple Cairo 1 contract exposing an entry point that calls the `send_message_to_l1` syscall with `to_address = 0x10000000000000000000000000000000000000000` (or any felt `>= 2^160`), analogous to the `test_contract` used in the repository's own tests: [5](#0-4) 
3. Submit an ordinary `invoke` transaction from any account calling that entry point.
4. Execution succeeds inside the syscall handler (confirmed by the existing test asserting `result.is_ok()` when `is_l3 == true`).
5. When the sequencer subsequently summarizes/serializes the call info (e.g., producing the transaction receipt or block output containing L2-to-L1 messages) via `OrderedL2ToL1Message::to_ordered_tuple`, the `.expect("Failed to convert L1Address to EthAddress")` panics, crashing the process handling that step.

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L428-438)
```rust
    pub fn send_message_to_l1(&mut self, message: MessageToL1) -> SyscallResult<()> {
        if !self.context.tx_context.block_context.chain_info.is_l3 {
            EthAddress::try_from(message.to_address)?;
        }
        let ordered_message_to_l1 =
            OrderedL2ToL1Message { order: self.context.n_sent_messages_to_l1, message };
        self.l2_to_l1_messages.push(ordered_message_to_l1);
        self.context.n_sent_messages_to_l1 += 1;

        Ok(())
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/send_message_to_l1.rs (L88-131)
```rust
#[rstest]
#[cfg_attr(feature = "cairo_native", case::native(RunnableCairo1::Native))]
#[case::vm(RunnableCairo1::Casm)]
fn test_send_message_to_l1_invalid_address(
    #[case] runnable_version: RunnableCairo1,
    #[values(true, false)] is_l3: bool,
) {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(runnable_version));
    let mut chain_info = ChainInfo::create_for_testing();
    chain_info.is_l3 = is_l3;
    let mut state = test_state(&chain_info, BALANCE, &[(test_contract, 1)]);

    let invalid_to_address = felt!("0x10000000000000000000000000000000000000000");
    let payload = vec![felt!(2019_u16), felt!(2020_u16)];
    let calldata = Calldata(
        concat(vec![
            vec![
                invalid_to_address,
                felt!(u64::try_from(payload.len()).expect("Failed to convert usize to u64.")),
            ],
            payload.clone(),
        ])
        .into(),
    );
    let entry_point_call = CallEntryPoint {
        entry_point_selector: selector_from_name("test_send_message_to_l1"),
        calldata,
        ..trivial_external_entry_point_new(test_contract)
    };
    let block_context = BlockContext::create_for_testing().with_chain_info(chain_info.clone());
    let result = entry_point_call.execute_directly_given_block_context(&mut state, block_context);

    if is_l3 {
        assert!(result.is_ok(), "Expected execution to succeed on L3 chain");
    } else {
        assert!(result.is_err(), "Expected execution to fail with invalid address");
        let error = result.unwrap_err();
        let error_string = error.to_string();
        assert!(
            error_string.contains("Out of range"),
            "Expected error containing 'Out of range', got: {error_string}"
        );
    }
}
```

**File:** crates/starknet_api/src/core.rs (L650-681)
```rust
#[derive(
    Debug, Copy, Clone, Default, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord,
)]
pub struct L1Address(pub Felt);

impl From<ContractAddress> for L1Address {
    fn from(address: ContractAddress) -> Self {
        L1Address(address.0.0)
    }
}

impl TryFrom<L1Address> for ContractAddress {
    type Error = StarknetApiError;

    fn try_from(address: L1Address) -> Result<Self, Self::Error> {
        Ok(ContractAddress(PatriciaKey::try_from(address.0)?))
    }
}

impl From<EthAddress> for L1Address {
    fn from(address: EthAddress) -> Self {
        L1Address(address.into())
    }
}

impl TryFrom<L1Address> for EthAddress {
    type Error = StarknetApiError;

    fn try_from(address: L1Address) -> Result<Self, Self::Error> {
        EthAddress::try_from(address.0)
    }
}
```

**File:** crates/blockifier/src/execution/call_info.rs (L92-121)
```rust
#[cfg_attr(any(test, feature = "testing"), derive(Clone))]
#[cfg_attr(feature = "transaction_serde", derive(serde::Deserialize))]
#[derive(Debug, Default, Eq, PartialEq, Serialize)]
pub struct OrderedL2ToL1Message {
    pub order: usize,
    pub message: MessageToL1,
}

impl OrderedItem for OrderedL2ToL1Message {
    type UnorderedItem = StarknetAPIMessageToL1;

    fn to_ordered_tuple(&self, from_address: ContractAddress) -> (usize, Self::UnorderedItem) {
        (
            self.order,
            StarknetAPIMessageToL1 {
                from_address,
                to_address: self
                    .message
                    .to_address
                    .try_into()
                    .expect("Failed to convert L1Address to EthAddress"),
                payload: self.message.payload.clone(),
            },
        )
    }

    fn get_items_from_call_execution(execution: &CallExecution) -> &[Self] {
        &execution.l2_to_l1_messages
    }
}
```
