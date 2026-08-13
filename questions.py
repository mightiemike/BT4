import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = "smartcontractkit/chainlink"
# todo: the name of the repository
REPO_NAME = "chainlink"

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # Chainlink Core Node web/API auth, session, routing, and privileged action surfaces
    # =================================================================================
    "core/web/router.go",
    "core/web/middleware.go",
    "core/web/auth/auth.go",
    "core/web/auth/gql.go",
    "core/web/auth/helpers.go",
    "core/web/api.go",
    "core/web/sessions_controller.go",
    "core/web/webauthn_controller.go",
    "core/web/user_controller.go",
    "core/web/jobs_controller.go",
    "core/web/pipeline_runs_controller.go",
    "core/web/pipeline_job_spec_errors_controller.go",
    "core/web/bridge_types_controller.go",
    "core/web/external_initiators_controller.go",
    "core/web/capability_controller.go",
    "core/web/vault_controller.go",
    "core/web/config_controller.go",
    "core/web/replay_controller.go",
    "core/web/evm_transactions_controller.go",
    "core/web/evm_transfer_controller.go",
    "core/web/chains_controller.go",
    "core/web/loop_registry.go",

    # =================================================================================
    # Authentication, user/session lifecycle, and identity federation
    # =================================================================================
    "core/auth/auth.go",
    "core/sessions/authentication.go",
    "core/sessions/session.go",
    "core/sessions/user.go",
    "core/sessions/webauthn.go",
    "core/sessions/localauth/orm.go",
    "core/sessions/oidcauth/oidc.go",
    "core/sessions/ldapauth/client.go",
    "core/sessions/ldapauth/sync.go",

    # =================================================================================
    # External adapter, bridge, job, workflow, and plugin execution trust boundaries
    # =================================================================================
    "core/bridges/bridge_type.go",
    "core/bridges/external_initiator.go",
    "core/bridges/orm.go",
    "core/services/functions/external_adapter_client.go",
    "core/services/functions/request.go",
    "core/services/functions/listener.go",
    "core/services/functions/connector_handler.go",
    "core/services/job/orm.go",
    "core/services/job/spawner.go",
    "core/services/job/validate.go",
    "core/services/job/workflow_spec_factory.go",
    "core/services/job/yaml_spec_factory.go",
    "core/services/job/wasm_file_spec_factory.go",
    "plugins/cmd.go",
    "plugins/env.go",
    "plugins/loop_registry.go",
    "plugins/registrar.go",

    # =================================================================================
    # Gateway, remote execution, vault, and workflow capability enforcement
    # =================================================================================
    "core/services/gateway/handler_factory.go",
    "core/services/gateway/handlers/common/requestcache.go",
    "core/services/gateway/handlers/capabilities/handler.go",
    "core/services/gateway/handlers/capabilities/v2/http_handler.go",
    "core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go",
    "core/services/gateway/handlers/capabilities/v2/response_cache.go",
    "core/services/gateway/handlers/confidentialrelay/handler.go",
    "core/services/gateway/handlers/functions/handler.functions.go",
    "core/services/gateway/handlers/functions/api.go",
    "core/services/gateway/handlers/vault/handler.go",
    "core/services/gateway/network/httpserver.go",
    "core/services/gateway/network/wsserver.go",
    "core/capabilities/webapi/outgoing_connector_handler.go",
    "core/capabilities/webapi/trigger/trigger.go",
    "core/capabilities/webapi/target/target.go",
    "core/capabilities/remote/executable/server.go",
    "core/capabilities/remote/executable/client.go",
    "core/capabilities/remote/executable/hasher.go",
    "core/capabilities/remote/dispatcher.go",
    "core/capabilities/remote/parallel_executor.go",
    "core/capabilities/transmission/local_executable_capability.go",
    "core/capabilities/vault/authorizer.go",
    "core/capabilities/vault/jwt_based_auth.go",
    "core/capabilities/vault/request_replay_guard.go",
    "core/capabilities/vault/validator.go",
    "core/capabilities/vault/gateway_vault_request_processor.go",
    "core/capabilities/vault/gw_handler.go",
    "core/capabilities/vault/verify.go",
    "core/capabilities/vault/workflow_owner_derivation.go",

    # =================================================================================
    # CCIP, OCR plugin, and cross-chain execution/validation surfaces in bounty scope
    # =================================================================================
    "core/capabilities/ccip/delegate.go",
    "core/capabilities/ccip/validate/validate.go",
    "core/capabilities/ccip/oraclecreator/bootstrap.go",
    "core/capabilities/ccip/oraclecreator/plugin.go",
    "core/capabilities/ccip/oraclecreator/wrapped_oracle.go",
    "core/capabilities/ccip/ocrimpls/config_tracker.go",
    "core/capabilities/ccip/ocrimpls/config_digester.go",
    "core/capabilities/ccip/ocrimpls/contract_transmitter.go",
    "core/capabilities/ccip/ocrimpls/evm_contract_transmitter.go",
    "core/capabilities/ccip/ocrimpls/svm_contract_transmitter.go",
    "core/capabilities/ccip/ocrimpls/aptos_contract_transmitter.go",
    "core/capabilities/ccip/ocrimpls/sui_contract_transmitter.go",
    "core/capabilities/ccip/ocrimpls/keyring.go",
    "core/capabilities/ccip/ccipevm/commitcodec.go",
    "core/capabilities/ccip/ccipevm/executecodec.go",
    "core/capabilities/ccip/ccipevm/extradatacodec.go",
    "core/capabilities/ccip/ccipevm/pluginconfig.go",
    "core/capabilities/ccip/ccipevm/manualexeclib/exec.go",
    "core/capabilities/ccip/ccipsolana/commitcodec.go",
    "core/capabilities/ccip/ccipsolana/executecodec.go",
    "core/capabilities/ccip/ccipsolana/extradatacodec.go",
    "core/capabilities/ccip/ccipsolana/pluginconfig.go",
    "core/capabilities/ccip/ccipaptos/commitcodec.go",
    "core/capabilities/ccip/ccipaptos/executecodec.go",
    "core/capabilities/ccip/ccipaptos/pluginconfig.go",
    "core/capabilities/ccip/ccipsui/executecodec.go",
]


target_scopes = [
    "Critical. An unprivileged external attacker can reach Chainlink Core Node web, session, auth, GraphQL, bridge, external-initiator, gateway, vault, workflow, or capability endpoints and achieve authentication bypass, privilege escalation, or unauthorized execution of privileged node actions.",
    "Critical. An unprivileged attacker can trigger arbitrary system command execution, arbitrary file read, sensitive local file disclosure, secret extraction, or unauthorized access to blockchain keys, database credentials, API tokens, or other confidential node material from a running Chainlink service.",
    "Critical. An unprivileged attacker can exploit job specs, workflow specs, bridge adapters, plugin registration, CCIP/OCR request handling, or capability routing to inject unauthorized external calls, tamper with execution, or cause misreporting of prices/data or malicious cross-chain message handling.",
    "Critical. An unprivileged attacker can abuse vault, gateway, remote executable, or workflow authorization flaws to bypass replay protection, request binding, owner scoping, or signature validation and gain access to protected secrets or privileged workflow actions.",
    "High. An unprivileged attacker can cause permanent or repeated denial of key Chainlink node functions, unsafe transaction submission, unauthorized fund movement, or material rate-limit bypass with real security impact.",
    "High. An unprivileged attacker can exploit parser, codec, request-binding, or chain-selection differentials in CCIP, OCR, gateway, or web handlers to make trusted validation logic accept attacker-controlled data differently from downstream execution.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one chainlink target.

    ```
    target_file format:
    "'File Name: core/web/router.go -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact chainlink target:

    {target_file}

    Project focus:
    Chainlink is a production oracle and cross-chain infrastructure codebase. Focus on unauthenticated or low-privilege attack paths in Core Node web/API auth, sessions, bridges, external initiators, job/workflow ingestion, gateway/capability routing, vault secret flows, plugin loading, and CCIP/OCR request execution.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Go symbols and real request/handler/spec names when possible.
    * Attacker is unprivileged only: no node operator/admin, no leaked keys, no malicious node/peer, no social engineering, no governance control, and no privileged infrastructure access.
    * Allowed attacker inputs are only normal external surfaces: HTTP/WebSocket/API requests, login/session flows, GraphQL queries, bridge or external-initiator interactions, job/workflow spec content, gateway/vault/capability messages, plugin/env inputs, and CCIP/OCR message or config data accepted by the node.
    * Ignore tests, mocks, docs, generated files, config-only findings, and dependency-only issues.
    * Do not rely on operator-only deployment mistakes, local shell access, or assumptions that a trusted DON participant is malicious.
    * Generate 12 to 16 high-signal questions.
    * At least 70% must target auth bypass, privilege escalation, unauthorized job/workflow execution, arbitrary command/file access, secret disclosure, request replay/binding bypass, parser or codec differentials, or CCIP/OCR message-validation failures.
    * Every question must be testable by unit test, integration test, fuzz test, invariant test, or differential test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authentication and authorization must bind every privileged action to the correct user, role, workflow owner, chain, job, and request.
    * Untrusted external input must never become privileged job execution, remote capability execution, bridge calls, vault access, or CCIP/OCR side effects without the intended checks.
    * Secrets stay secret: blockchain keys, database credentials, API tokens, session material, vault contents, and sensitive config must not become readable or exfiltratable.
    * Replay, signature, ownership, and namespace checks must not be bypassable through ID rewriting, stale state, parser tricks, or cross-request confusion.
    * Chain-specific message validation must not accept attacker-controlled data that can cause false reports, unsafe execution, or unauthorized fund movement.

    Each question must include:
    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: unit/integration/fuzz PARAMETERS and assert AUTHZ_BOUNDARY, SECRET_ISOLATION, REQUEST_BINDING, or CHAIN_EXECUTION_SAFETY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused chainlink exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: no node-operator/admin privilege, no leaked keys, no social engineering, and no malicious node/peer/operator assumptions.
- Reject anything that depends only on test/mock/config/docs/generated files, dependency bugs alone, direct store mutation from tests, or best-practice cleanup without exploitable impact.
- Focus on real Chainlink compromise paths reachable from ordinary web/API requests, session/login flows, GraphQL, bridge and external-initiator input, job/workflow specs, gateway or vault messages, plugin/env input, and CCIP/OCR request paths.

## Validate
- Trace the exact reachable path from the attacker input into auth/session handling, job/workflow execution, bridge/external adapter calls, gateway/capability handlers, vault secret flows, plugin registration, or CCIP/OCR execution.
- Check whether existing authz, signature, replay, namespace, rate-limit, role, chain-binding, or parser/codec checks already stop it.
- Accept only real auth bypass, privilege escalation, arbitrary command or file action, secret disclosure, unauthorized transaction or workflow execution, misreporting/data tampering, or direct node compromise behavior.
- Require exact file/function support and a reproducible unit/integration/fuzz/invariant PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and matching Chainlink bounty impact]

### Likelihood Explanation
[Preconditions, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Unit/integration test or fuzz/invariant test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for chainlink security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-node, malicious-peer, operator-only, leaked-key, dependency-only, docs/style, generated-file, test/mock/config-only, self-XSS-only, and purely theoretical issues.
- Reject if the exploit needs victim social engineering, impossible setup, or unsupported behavior outside normal Chainlink inputs.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged user, unless the claim proves privilege escalation from an unprivileged path.
- The final impact must map to an in-scope Chainlink impact such as auth bypass, arbitrary system command execution, sensitive file or secret disclosure, unauthorized privileged node action, misreporting of prices/data, unsafe CCIP/OCR execution, or direct compromise of node or funds.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood.
6. Reproducible proof path: unit PoC, integration test, invariant/fuzz test, or exact manual steps.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user trigger this through real web/API, bridge, job/workflow, gateway, vault, plugin, or CCIP/OCR surfaces without privileged access?
- Does the code actually behave as claimed?
- Is the impact caused by this code, not by a malicious node, peer, repository operator already holding privileged machine access, or dependency alone?
- Is the unauthorized execution, disclosure, bypass, or local/project compromise concrete, not hypothetical?
- Would a Chainlink bounty triager accept the proof?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and Chainlink bounty category]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/integration test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for chainlink.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-user analogs in auth/session bypass, bridge or external-adapter abuse, job/workflow ingestion, gateway/vault/capability routing, CCIP/OCR validation, arbitrary command execution, or secret disclosure trust boundaries.
- Reject malicious-node/peer/operator analogs, mocked-only paths, dependency-only bugs, and no-impact or self-XSS-only analogs.

## Validate
- Map the bug class to the strongest reachable chainlink path.
- Prove root cause with exact file/function support.
- Accept only concrete auth bypass, unauthorized privileged node action, arbitrary shell/file action, secret disclosure, unsafe transaction/workflow execution, misreporting/data tampering, or direct node compromise impact.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
