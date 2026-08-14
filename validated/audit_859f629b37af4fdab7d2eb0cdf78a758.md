## Title
Front-runnable two-step deployment of the CCIP Signer Registry (Solana) allows arbitrary account to seize program ownership - (File: `deployment/ccip/changeset/ccip-attestation-solana/cs_deploy_signer_registry_solana.go`)

### Summary
The CCIP Signer Registry program on Solana is deployed and initialized via two independent, separately-invoked changesets, `DeployBaseSignerRegistryContractChangeset` and `InitializeBaseSignerRegistryContractChangeset`. Because the program's `Config`/`Signers` state accounts are deterministic PDAs derived only from the program ID (not from any deployer-specific seed), and because the on-chain `initialize` instruction's own documentation states that ownership enforcement is conditional on an optional `init_guard` feature, an attacker who observes the just-deployed, not-yet-initialized program can send their own `initialize` transaction first and become the permanent registry owner — exactly the front-running scenario described in the referenced report (deploy proxy/program, then a separate transaction claims ownership before the legitimate owner's initialization lands).

### Finding Description
`DeployBaseSignerRegistryContractChangeset` uploads/deploys the `signer_registry` Solana program bytecode in one transaction/changeset run: [1](#0-0) 

A separate changeset, `InitializeBaseSignerRegistryContractChangeset`, is required afterward to actually initialize the program's state: [2](#0-1) 

The `Config` and `Signers` accounts touched by `initialize` are computed as deterministic Program Derived Addresses using fixed seeds (`"config"`, `"signers"`) and the program ID alone: [3](#0-2) 

This means as soon as the program bytecode is live on-chain (after `DeployBaseSignerRegistryContractChangeset` completes but before `InitializeBaseSignerRegistryContractChangeset` runs), the exact addresses of the yet-to-be-created state accounts are publicly computable. The `initialize` instruction binding's own documentation confirms that the signing `authority` becomes the permanent program owner, and that authority is only restricted to the program's upgrade authority when an optional `init_guard` feature is enabled — implying that without it, any signer can call `initialize` and become owner: [4](#0-3) 

This is the same root-cause pattern as the reported `EnsoWalletFactory` bug: deployment and initialization are split into two separate, non-atomic transactions, leaving a window during which any third party can call the permissionless/under-guarded `initialize` entry point and claim ownership before the intended operator does. Contrast this with the safe pattern used elsewhere in the same codebase for EVM upgradeable proxies, where `initData` (the `initialize` calldata) is passed directly into the proxy's constructor call so that deployment and initialization happen atomically in a single transaction: [5](#0-4) 

### Impact Explanation
If an attacker's `initialize` transaction lands before the legitimate `InitializeBaseSignerRegistryContractChangeset` call, the attacker becomes the registry's owner/authority. Since the Signer Registry manages the set of authorized signers used for CCIP attestation validation (add/remove/promote signer instructions gated by `authority == owner`), attacker-controlled ownership could let them add malicious signer keys or block legitimate signer management, directly compromising the CCIP attestation trust boundary this registry is meant to protect. This maps to "unauthorized privileged node action" / "misreporting/data tampering" via a corrupted trust root.

### Likelihood Explanation
Exploitability depends on: (1) the deploy and initialize changesets actually being run as separate transactions/operator actions in production (as the code structure enforces), and (2) whether the on-chain program has `init_guard` enabled to restrict `authority` to the upgrade authority. The binding's own doc comment shows this restriction is optional/feature-gated rather than unconditional, so the safety of this flow depends entirely on off-chain program build configuration not visible in this repository. Given the deterministic PDA derivation and the documented conditional protection, this is a plausible, non-trivial race condition rather than a purely theoretical one.

### Recommendation
- Combine deployment and initialization into a single atomic transaction/instruction sequence (as is already done correctly for the Solana forwarder's `DeployForwarderSeq`, which creates the state account and initializes it within the same operation) instead of two independently-invokable changesets.
- Ensure the on-chain `initialize` instruction unconditionally requires `authority` to match the program's upgrade authority (i.e., make `init_guard` mandatory, not optional) so no third party can win a front-running race regardless of deployment sequencing.
- Add a post-initialize verification step in `InitializeBaseSignerRegistryContractChangeset`/`DeployBaseSignerRegistryContractChangeset` that fails loudly if the `Config` account already exists and is owned by an unexpected authority, to detect (if not prevent) a front-run.

### Proof of Concept
1. Operator runs `DeployBaseSignerRegistryContractChangeset` for a target chain — program bytecode becomes live at a known program ID. [1](#0-0) 
2. Before the operator runs `InitializeBaseSignerRegistryContractChangeset`, an attacker computes `configPda`/`signersPda` from the known program ID using the same fixed seeds (`"config"`, `"signers"`), builds their own `initialize` instruction naming themselves as `authority`, and submits it first. [6](#0-5) 
3. If the on-chain program does not enforce `init_guard` (upgrade-authority check), the attacker's transaction succeeds, creating the `Config`/`Signers` accounts with the attacker as owner.
4. The legitimate `InitializeBaseSignerRegistryContractChangeset` call subsequently fails (accounts already exist) or, depending on program logic, is a no-op — the attacker retains permanent control over signer management for CCIP attestation.

### Citations

**File:** deployment/ccip/changeset/ccip-attestation-solana/cs_deploy_signer_registry_solana.go (L48-75)
```go
func DeployBaseSignerRegistryContractChangeset(e cldf.Environment, c DeployBaseSignerRegistryContractConfig) (cldf.ChangesetOutput, error) {
	e.Logger.Infow("Deploying base signer registry", "chain_selector", c.ChainSelector)
	err := c.Validate(e)
	if err != nil {
		return cldf.ChangesetOutput{}, fmt.Errorf("failed to deploy base signer registry contract: %w", err)
	}
	chainSel := c.ChainSelector
	chain := e.BlockChains.SolanaChains()[chainSel]

	newAddresses := cldf.NewMemoryAddressBook()

	programFileName := solutils.ProgBaseSignerRegistry + ".so"
	programFilePath := filepath.Join(chain.ProgramsPath, programFileName)
	if _, err := os.Stat(programFilePath); err != nil {
		if !os.IsNotExist(err) {
			return cldf.ChangesetOutput{}, fmt.Errorf("failed to check existing program artifact: %w", err)
		}
		if strings.TrimSpace(c.WorkflowRun) == "" || strings.TrimSpace(c.ArtifactID) == "" {
			return cldf.ChangesetOutput{}, fmt.Errorf("program artifact %s not found in %s and workflow run/artifact ID not provided", programFileName, chain.ProgramsPath)
		}
		if err := DownloadReleaseArtifactsFromGithubWorkflowRun(context.Background(), c.WorkflowRun, c.ArtifactID, chain.ProgramsPath); err != nil {
			return cldf.ChangesetOutput{}, fmt.Errorf("failed to download release artifacts: %w", err)
		}
	}
	_, err = deployBaseSignerRegistryContract(e, chain, newAddresses, c)
	if err != nil {
		return cldf.ChangesetOutput{}, fmt.Errorf("failed to deploy base signer registry contract: %w", err)
	}
```

**File:** deployment/ccip/changeset/ccip-attestation-solana/cs_deploy_signer_registry_solana.go (L88-124)
```go
func InitializeBaseSignerRegistryContractChangeset(e cldf.Environment, c InitializeBaseSignerRegistryContractConfig) (cldf.ChangesetOutput, error) {
	e.Logger.Infow("Initializing base signer registry", "chain_selector", c.ChainSelector)
	err := c.Validate(e)
	if err != nil {
		return cldf.ChangesetOutput{}, fmt.Errorf("failed to initialize base signer registry contract: %w", err)
	}
	chainSel := c.ChainSelector
	chain := e.BlockChains.SolanaChains()[chainSel]
	authority := chain.DeployerKey.PublicKey()

	configPda, _, _ := solana.FindProgramAddress([][]byte{[]byte("config")}, signer_registry.ProgramID)
	signersPda, _, _ := solana.FindProgramAddress([][]byte{[]byte("signers")}, signer_registry.ProgramID)
	eventAuthorityPda, _, _ := solana.FindProgramAddress([][]byte{[]byte("__event_authority")}, signer_registry.ProgramID)
	programData, err := getSolProgramData(e, chain, signer_registry.ProgramID)
	if err != nil {
		return cldf.ChangesetOutput{}, err
	}

	ix, err := signer_registry.NewInitializeInstruction(
		authority,
		solana.SystemProgramID,
		configPda,
		signersPda,
		signer_registry.ProgramID,
		programData.Address,
		eventAuthorityPda,
		signer_registry.ProgramID,
	)
	if err != nil {
		return cldf.ChangesetOutput{}, fmt.Errorf("failed to initialize base signer registry contract: %w", err)
	}

	if err := chain.Confirm([]solana.Instruction{ix}); err != nil {
		return cldf.ChangesetOutput{}, fmt.Errorf("failed to initialize base signer registry contract: %w", err)
	}

	return cldf.ChangesetOutput{}, nil
```

**File:** deployment/ccip/shared/bindings/signer_registry_solana/instructions.go (L105-145)
```go
// Builds a "initialize" instruction.
// Initializes the CCIP Signer Registry program with initial configuration and owner. //  // This instruction must be called once after program deployment to set up the program's // state accounts. It creates both the Config and Signers accounts with their respective // Program Derived Addresses (PDAs). The authority account signing this transaction will // be set as the program owner. //  // # Parameters //  // * `ctx` - The context containing all accounts required for initialization: // - `authority`: The signer who is deploying/initializing the program. This account will // become the program owner with exclusive authority to manage signers and propose // ownership transfers. When the `init_guard` feature is enabled, this must be the // program's upgrade authority. // - `system_program` ... (truncated)
func NewInitializeInstruction(
	authorityAccount solanago.PublicKey,
	systemProgramAccount solanago.PublicKey,
	configAccount solanago.PublicKey,
	signersAccount solanago.PublicKey,
	programForVerificationAccount solanago.PublicKey,
	programDataAccount solanago.PublicKey,
	eventAuthorityAccount solanago.PublicKey,
	programAccount solanago.PublicKey,
) (solanago.Instruction, error) {
	accounts__ := solanago.AccountMetaSlice{}
	buf__ := new(bytes.Buffer)
	enc__ := binary.NewBorshEncoder(buf__)

	// Encode the instruction discriminator.
	err := enc__.WriteBytes(Instruction_Initialize[:], false)
	if err != nil {
		return nil, fmt.Errorf("failed to write instruction discriminator: %w", err)
	}

	// Add the accounts to the instruction.
	{
		// Account 0 "authority": Writable, Signer, Required
		accounts__.Append(solanago.NewAccountMeta(authorityAccount, true, true))
		// Account 1 "system_program": Read-only, Non-signer, Required
		accounts__.Append(solanago.NewAccountMeta(systemProgramAccount, false, false))
		// Account 2 "config": Writable, Non-signer, Required
		accounts__.Append(solanago.NewAccountMeta(configAccount, true, false))
		// Account 3 "signers": Writable, Non-signer, Required
		accounts__.Append(solanago.NewAccountMeta(signersAccount, true, false))
		// Account 4 "program_for_verification": Read-only, Non-signer, Required, Address: S1GN4jus9XzKVVnoHqfkjo1GN8bX46gjXZQwsdGBPHE
		accounts__.Append(solanago.NewAccountMeta(programForVerificationAccount, false, false))
		// Account 5 "program_data": Read-only, Non-signer, Required
		accounts__.Append(solanago.NewAccountMeta(programDataAccount, false, false))
		// Account 6 "event_authority": Read-only, Non-signer, Required
		accounts__.Append(solanago.NewAccountMeta(eventAuthorityAccount, false, false))
		// Account 7 "program": Read-only, Non-signer, Required
		accounts__.Append(solanago.NewAccountMeta(programAccount, false, false))
	}
```

**File:** deployment/ccip/changeset/v1_6_1/cs_transparent_upgradeable_proxy.go (L196-198)
```go
					address, tx, proxy, err := transparent_upgradeable_proxy.DeployTransparentUpgradeableProxy(
						chain.DeployerKey, chain.Client, config.BurnMintERC20Transparent, chain.DeployerKey.From, initData,
					)
```
