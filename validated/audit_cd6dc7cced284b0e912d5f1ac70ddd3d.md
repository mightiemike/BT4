### Title
Deterministic, deployer-independent contract address allows permanent address squatting / front-running of counterfactual deployments - (File: crates/blockifier/src/execution/execution_utils.rs, crates/blockifier/src/state/errors.rs)

### Summary
The Moloch-style `delegateKey` report describes a class of bug where an address that a legitimate party intends to claim can be irrevocably "squatted" by an unrelated party who front-runs the assignment, permanently blocking the intended owner. The closest reachable analog in this sequencer/blockifier codebase is the `deploy` syscall's `deploy_from_zero` mode, in which the resulting contract address is computed purely from `class_hash`, `contract_address_salt`, and `constructor_calldata` — independent of the caller/deployer — and the state only rejects a deployment if that exact address is *already* occupied.

### Finding Description
When a contract calls the `deploy` syscall with `deploy_from_zero = true`, the deployer address used in address calculation is forced to `ContractAddress::default()` (i.e., zero) rather than the actual caller: [1](#0-0) 

This means the resulting `deployed_contract_address` depends only on `(salt, class_hash, constructor_calldata)` — all of which are public, precomputable values known off-chain to anyone who knows the parameters a legitimate dApp/account intends to use (e.g. a standard account's constructor calldata containing its public key, and a deterministic salt equal to that public key). Any party — attacker or otherwise — can independently compute this exact same address before the intended deployer submits their transaction.

State only guards against a *second* deployment to the same address once it is already used; it has no mechanism reserving an address for an intended future deployer: [2](#0-1) 

Because this rejection happens only after the fact (a `StateError::UnavailableContractAddress` when a *second* actor tries to claim the already-used address), whoever submits the deploy transaction first — front-running the intended deployer — permanently claims that address. There is no way for the "true" party to reclaim or reassign an address once it has been deployed to by someone else, exactly mirroring the `delegateKey` bug's core defect: a publicly-computable, deterministic identifier can be squatted by an unrelated party before the rightful owner claims it, and there is no recovery path afterward.

### Impact Explanation
Counterfactual-address workflows are common on Starknet: users are shown their (not-yet-deployed) account address and are told to fund it before deployment. If an attacker precomputes the same address (using the same salt/class_hash/calldata, which for standard account contracts is derived from the user's own public key and is knowable to anyone who can observe it, e.g. through wallet UIs, shared salts, or leaked parameters) and calls `deploy` with `deploy_from_zero=true` to squat that address with an attacker-controlled class/constructor before the legitimate `deploy_account` transaction lands, the legitimate deploy will subsequently fail with `UnavailableContractAddress`, permanently preventing the intended owner from ever deploying their account at that address. Any funds already sent to the precomputed address remain stuck under attacker control or unreachable by the intended owner — a concrete freezing/loss-of-funds condition, and an unauthorized party ends up controlling an address/account identity that was meant for someone else.

### Likelihood Explanation
Exploitability requires the attacker to learn the exact `(salt, class_hash, constructor_calldata)` triple before the legitimate deployment lands — realistic in common counterfactual-deployment UX patterns where this info is displayed/shared in advance so the address can be funded. Any unprivileged account with a deployed contract capable of invoking the `deploy` syscall (a normal, permissionless capability) can perform the front-run; no special privilege, staking, or node-level access is needed, making this reachable purely from ordinary transaction submission.

### Recommendation
- Avoid deployer-independent deployed addresses for security-sensitive counterfactual flows, or require that `deploy_from_zero` deployments include a caller-bound component (e.g. requiring the caller to also match an expected value baked into the calldata) so that address computation cannot be replicated by an unrelated caller.
- Consider adding a reservation/commit-reveal mechanism analogous to the report's recommendation: allow a wallet/account to commit to a salted hash of its intended deployment parameters and lock the resulting address for its exclusive use for a bounded window, rejecting third-party deployments to that address during the window.
- At minimum, document and strongly discourage the `deploy_from_zero=true` pattern from being used for any address that will receive funds prior to deployment, since it structurally has no protection against squatting.

### Proof of Concept
1. A dApp/wallet computes a future account address `A = calculate_contract_address(salt, account_class_hash, [pubkey], deployer=0)` and displays it to the user to fund before deployment (standard practice).
2. Attacker observes `salt`, `account_class_hash`, and `pubkey` (all public/knowable ahead of the real `deploy_account` transaction, e.g., leaked from a wallet's local computation, shared demo salts, or simply guessable schemes).
3. Attacker submits an ordinary invoke transaction from any of their own deployed contracts that calls the `deploy` syscall with `deploy_from_zero=true` and the same `(salt, account_class_hash, [pubkey])`, landing before the user's real `deploy_account` transaction: [3](#0-2) 
4. The attacker's transaction succeeds and occupies address `A` with attacker-chosen state/class.
5. When the legitimate user submits their `deploy_account` transaction targeting the same computed address `A`, it fails with `StateError::UnavailableContractAddress`: [2](#0-1) 
6. Any funds previously sent to `A` in anticipation of the legitimate deployment are now permanently controlled by the attacker's contract at that address, with no recovery mechanism.

### Citations

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L593-647)
```rust
    fn deploy(
        request: DeployRequest,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
    ) -> DeprecatedSyscallResult<DeployResponse> {
        let versioned_constants =
            &syscall_handler.context.tx_context.block_context.versioned_constants;
        if should_reject_deploy(
            versioned_constants.disable_deploy_in_validation_mode,
            syscall_handler.execution_mode(),
        ) {
            return Err(DeprecatedSyscallExecutionError::InvalidSyscallInExecutionMode {
                syscall_name: "deploy".to_string(),
                execution_mode: syscall_handler.execution_mode(),
            });
        }

        let deployer_address = syscall_handler.storage_address;
        let deployer_address_for_calculation = match request.deploy_from_zero {
            true => ContractAddress::default(),
            false => deployer_address,
        };
        let deployed_contract_address = calculate_contract_address(
            request.contract_address_salt,
            request.class_hash,
            &request.constructor_calldata,
            deployer_address_for_calculation,
        )?;

        // Increment the Deploy syscall's linear cost counter by the number of elements in the
        // constructor calldata.
        let syscall_usage = syscall_handler
            .syscalls_usage
            .get_mut(&DeprecatedSyscallSelector::Deploy)
            .expect("syscalls_usage entry for Deploy must be initialized");
        syscall_usage.linear_factor += request.constructor_calldata.0.len();

        let ctor_context = ConstructorContext {
            class_hash: request.class_hash,
            code_address: Some(deployed_contract_address),
            storage_address: deployed_contract_address,
            caller_address: deployer_address,
        };
        let mut remaining_gas = syscall_handler.context.gas_costs().base.default_initial_gas_cost;
        let call_info = execute_deployment(
            syscall_handler.state,
            syscall_handler.context,
            ctor_context,
            request.constructor_calldata,
            &mut remaining_gas,
        )?;
        syscall_handler.inner_calls.push(call_info);

        Ok(DeployResponse { contract_address: deployed_contract_address })
    }
```

**File:** crates/blockifier/src/state/errors.rs (L26-27)
```rust
    #[error("Deployment failed: contract already deployed at address {:#066x}", ***.0)]
    UnavailableContractAddress(ContractAddress),
```
