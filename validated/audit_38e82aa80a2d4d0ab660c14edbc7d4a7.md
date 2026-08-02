[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** third_party/move/move-core/types/src/vm_status.rs (L62-66)
```rust
    Error {
        status_code: StatusCode,
        sub_status: Option<u64>,
        message: Option<String>,
    },
```

**File:** third_party/move/move-core/types/src/vm_status.rs (L288-298)
```rust
            VMStatus::Error {
                status_code: code,
                message,
                ..
            } => {
                match code.status_type() {
                    // Any unknown error should be discarded
                    StatusType::Unknown => Err(code),
                    // Any error that is a validation status (i.e. an error arising from the prologue)
                    // causes the transaction to not be included.
                    StatusType::Validation => Err(code),
```

**File:** types/src/transaction/mod.rs (L1891-1920)
```rust
    pub fn from_vm_status(
        vm_status: VMStatus,
        features: &Features,
        memory_limit_exceeded_as_miscellaneous_error: bool,
    ) -> Self {
        let status_code = vm_status.status_code();
        // TODO: keep_or_discard logic should be deprecated from Move repo and refactored into here.
        match vm_status.keep_or_discard(
            features.is_enabled(FeatureFlag::ENABLE_FUNCTION_VALUES),
            memory_limit_exceeded_as_miscellaneous_error,
            features.is_enabled(FeatureFlag::VM_BINARY_FORMAT_V10),
        ) {
            Ok(recorded) => match recorded {
                // TODO(bowu):status code should be removed from transaction status
                KeptVMStatus::MiscellaneousError => {
                    Self::Keep(ExecutionStatus::MiscellaneousError(Some(status_code)))
                },
                _ => Self::Keep(recorded.into()),
            },
            Err(code) => {
                if code.status_type() == StatusType::InvariantViolation
                    && features.is_enabled(FeatureFlag::CHARGE_INVARIANT_VIOLATION)
                {
                    Self::Keep(ExecutionStatus::MiscellaneousError(Some(code)))
                } else {
                    Self::Discard(code)
                }
            },
        }
    }
```

**File:** aptos-move/e2e-testsuite/src/tests/invariant_violation.rs (L33-60)
```rust
    // CHARGE_INVARIANT_VIOLATION enabled at genesis so this txn is kept.
    assert_eq!(
        output.status(),
        &TransactionStatus::Keep(ExecutionStatus::MiscellaneousError(Some(
            StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR
        ))),
    );

    // Disable the CHARGE_INVARIANT_VIOLATION flag.
    executor.exec("features", "change_feature_flags_internal", vec![], vec![
        MoveValue::Signer(AccountAddress::ONE)
            .simple_serialize()
            .unwrap(),
        MoveValue::Vector(vec![]).simple_serialize().unwrap(),
        MoveValue::Vector(vec![MoveValue::U64(
            FeatureFlag::CHARGE_INVARIANT_VIOLATION as u64,
        )])
        .simple_serialize()
        .unwrap(),
    ]);

    let output = executor.execute_transaction(txn);

    // With CHARGE_INVARIANT_VIOLATION disabled this transaction will be discarded.
    assert_eq!(
        output.status(),
        &TransactionStatus::Discard(DiscardedVMStatus::UNKNOWN_INVARIANT_VIOLATION_ERROR),
    );
```

**File:** mempool/src/shared_mempool/tasks.rs (L612-620)
```rust
                    Some(validation_status) => {
                        statuses.push((
                            transaction.clone(),
                            (
                                MempoolStatus::new(MempoolStatusCode::VmError),
                                Some(validation_status),
                            ),
                        ));
                    },
```
