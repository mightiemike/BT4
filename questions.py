import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 22
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'Shopify/shopify-app-js'
# todo: the name of the repository
REPO_NAME = 'shopify-app-js'

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
    # HMAC / signature verification: webhooks, request HMAC, app proxy, timing-safe compare
    # =================================================================================
    "packages/apps/shopify-api/lib/utils/hmac-validator.ts",
    "packages/apps/shopify-api/lib/utils/get-hmac-key.ts",
    "packages/apps/shopify-api/lib/auth/oauth/safe-compare.ts",
    "packages/apps/shopify-api/lib/webhooks/validate.ts",
    "packages/apps/shopify-api/lib/webhooks/process.ts",
    "packages/apps/shopify-api/lib/flow/validate.ts",
    "packages/apps/shopify-api/lib/fulfillment-service/validate.ts",
    "packages/apps/shopify-api/runtime/crypto/index.ts",
    "packages/apps/shopify-api/runtime/crypto/utils.ts",

    # =================================================================================
    # Session token (JWT) decode & validation: signature, aud/iss/exp/dest claims
    # =================================================================================
    "packages/apps/shopify-api/lib/session/decode-session-token.ts",
    "packages/apps/shopify-api/lib/session/session-utils.ts",
    "packages/apps/shopify-api/lib/session/session.ts",
    "packages/apps/shopify-api/lib/session/classes.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-session-token-header.ts",
    "packages/apps/shopify-app-express/src/helpers/get-session-token.ts",

    # =================================================================================
    # OAuth flow: begin/callback, nonce (CSRF state), signed cookies, token grants
    # =================================================================================
    "packages/apps/shopify-api/lib/auth/oauth/oauth.ts",
    "packages/apps/shopify-api/lib/auth/oauth/nonce.ts",
    "packages/apps/shopify-api/lib/auth/oauth/create-session.ts",
    "packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts",
    "packages/apps/shopify-api/lib/auth/oauth/client-credentials.ts",
    "packages/apps/shopify-api/lib/auth/oauth/refresh-token.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts",
    "packages/apps/shopify-app-express/src/auth/auth-callback.ts",

    # =================================================================================
    # Shop/host sanitization, redirect and host decoding: open redirect, SSRF, allowlist bypass
    # =================================================================================
    "packages/apps/shopify-api/lib/utils/shop-validator.ts",
    "packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts",
    "packages/apps/shopify-api/lib/utils/domain-transformer.ts",
    "packages/apps/shopify-api/lib/auth/decode-host.ts",
    "packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts",

    # =================================================================================
    # Unprivileged HTTP entry points: request authentication, app-proxy / public surfaces
    # =================================================================================
    "packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/public/checkout/authenticate.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/public/customer-account/authenticate.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/flow/authenticate.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts",
    "packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts",
    "packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts",
    "packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts",
    "packages/apps/shopify-app-express/src/webhooks/process.ts",

    # =================================================================================
    # Cookies and low-level HTTP request/response handling shared by every entry point
    # =================================================================================
    "packages/apps/shopify-api/runtime/http/cookies.ts",
    "packages/apps/shopify-api/runtime/http/utils.ts",
    "packages/apps/shopify-api/runtime/http/headers.ts",
    "packages/apps/shopify-api/lib/utils/processed-query.ts",

    # =================================================================================
    # Session storage: query construction where session id / shop reach the datastore
    # =================================================================================
    "packages/apps/session-storage/shopify-app-session-storage-mysql/src/mysql.ts",
    "packages/apps/session-storage/shopify-app-session-storage-postgresql/src/postgresql.ts",
    "packages/apps/session-storage/shopify-app-session-storage-sqlite/src/sqlite.ts",

    # =================================================================================
    # API clients & GraphQL proxy: outbound URL/host construction and credential handling
    # =================================================================================
    "packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts",
    "packages/apps/shopify-api/lib/clients/common.ts",
    "packages/apps/shopify-api/lib/utils/fetch-request.ts",
    "packages/api-clients/admin-api-client/src/validations.ts",
    "packages/api-clients/graphql-client/src/graphql-client/http-fetch.ts",
]


target_scopes = [
    "Critical. An unprivileged attacker who can only send HTTP requests to a Shopify app built with this library forges a valid session token (JWT) or app-proxy/session context for a shop they do not control, by defeating signature verification, `aud`/`iss`/`dest`/`exp` claim checks in decodeSessionToken, or the JWT/HMAC key derivation in getHMACKey, gaining an authenticated admin or storefront-customer context as another merchant or user.",
    "Critical. An unprivileged attacker forges a webhook, Flow, fulfillment-service, or app-proxy request that the library accepts as genuinely signed by Shopify, by bypassing HMAC verification in validateHmac/validateHmacFromRequestFactory, the raw-body handling in webhooks/process, or the timing-safe comparison in safeCompare, causing the app to perform state changes or leak data on behalf of an unverified sender.",
    "Critical. An unprivileged attacker hijacks or fixates a merchant's OAuth install/authentication, by defeating the nonce/state CSRF check or the signed OAuth cookie in the begin/callback flow (oauth.ts, nonce.ts, create-session.ts), the callback HMAC, or the token-exchange/client-credentials grant, so the attacker binds their own session/token to a victim shop or obtains an access token for a shop they do not own.",
    "Critical. An unprivileged attacker escalates to another merchant's data or actions across tenant boundaries, by making the session lookup, shop-to-session mapping, or session persistence in session-utils / a session-storage adapter return, overwrite, or accept a session for a shop other than the one the request actually authenticated as.",
    "Advanced. An unprivileged attacker injects into a session-storage query, by passing a crafted session id, shop domain, or state value that is concatenated into SQL (mysql.ts, postgresql.ts, sqlite.ts) rather than safely parameterized, reading or corrupting stored sessions and access tokens of other shops.",
    "Advanced. An unprivileged attacker turns the app into an open redirect or SSRF primitive, by smuggling an attacker-controlled shop/host/redirect value past sanitizeShop, sanitizeHost, decodeHost, validate-redirect-url, or the outbound URL construction in the API clients / graphqlProxy, redirecting an admin to attacker infrastructure (leaking session tokens) or aiming an authenticated Admin API request at an attacker host.",
    "Advanced. A remote attacker with no credentials causes denial of service or an uncaught fatal error in a request-authentication path, by sending crafted headers, query strings, JWTs, HMAC payloads, or host/shop parameters that trigger unbounded work, catastrophic regex backtracking in the shop/host validators, or an unhandled exception inside an authenticate/validate handler on the default configuration.",
    "Intermediate. An unprivileged attacker bypasses an authorization or embedding gate that the library is responsible for enforcing, by evading ensure-installed-on-shop, validate-authenticated-session, reject-bot-request, or the bot/OPTIONS/CORS pre-checks, reaching an authenticated route or a merchant's app context without a valid session token.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one shopify-app-js target.

    ```
    target_file format:
    "'File Name: packages/apps/shopify-api/lib/utils/hmac-validator.ts -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact shopify-app-js target:

    {target_file}

    Project focus:
    shopify-app-js is the official set of libraries (@shopify/shopify-api, shopify-app-remix, shopify-app-react-router, shopify-app-express, session-storage adapters, api-clients) that a Shopify app's backend uses to authenticate requests. Focus on session-token (JWT) verification, OAuth begin/callback nonce+HMAC+cookie handling, webhook/Flow/app-proxy HMAC verification, shop/host/redirect sanitization, session storage/lookup, and the code that builds authenticated outbound Admin/Storefront API requests.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact TypeScript symbols (function, class, method, constant, config field) when possible.
    * Attacker is unprivileged only: an anonymous HTTP/webhook/app-proxy client, or a logged-in merchant/customer of ONE shop, sending crafted requests to an app built with these libraries.
    * Attacker is NOT the app developer, a Shopify employee, or a privileged operator; does NOT possess the app's `apiSecretKey`, private keys, or any leaked secret; and cannot rely on MITM, local-network, physical access, or social engineering.
    * Ignore test files, mocks, fixtures, docs/example files, generated code, and build/CI/config.
    * Ignore self-harm (attacker affecting only their own shop) and pure best-practice/style critique.
    * Generate 12 to 16 high-signal questions.
    * At least 70% must target session-token/HMAC/signature forgery, OAuth CSRF or token theft, cross-tenant session access, injection into session storage, open redirect/SSRF, or DoS in an authentication handler.
    * Every question must be testable by a Jest test, a crafted HTTP/webhook/app-proxy request, a forged JWT or HMAC payload, or a fuzz/differential test over encoded inputs.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authenticity is proven: a request is treated as coming from Shopify or an authenticated merchant only after its HMAC or JWT signature verifies against the app secret with a constant-time comparison, and all required claims (`aud`, `iss`, `dest`, `exp`, `nbf`) are checked.
    * Sessions are tenant-isolated: a verified request resolves only to a session for the exact shop it authenticated as; storage lookups never return or overwrite another shop's session or access token.
    * CSRF state is enforced: the OAuth callback is bound to the nonce/state and signed cookie issued at begin, and cannot be replayed or fixated by a third party.
    * Destinations are constrained: any shop/host/redirect derived from request input is validated against the allowed-domain rules before it is used in a redirect, cookie, or outbound API URL.
    * Secrets stay internal: the app secret, access tokens, and session material never reach a redirect target, response body, log line, or error surface an attacker can read.

    Each question must include:
    1. target module/function;
    2. attacker action;
    3. preconditions;
    4. request/call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: module.function] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger REQUEST_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: Jest/HTTP/webhook/forged-JWT/HMAC INPUTS and assert AUTHENTICITY, TENANT_ISOLATION, CSRF_STATE, DESTINATION_ALLOWLIST, or SECRET_CONFINEMENT.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused shopify-app-js exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: an anonymous HTTP/webhook/app-proxy client, or a single merchant/customer sending crafted requests to an app built with these libraries. No app-developer role, no Shopify-employee role, no `apiSecretKey` or leaked secret. No MITM, local-network, physical-access, or social-engineering assumptions.
- Reject anything requiring privileged access, a stolen secret, non-default library configuration, or a bug in the host app rather than in this library.
- Reject anything that depends only on test/mock/fixture/docs/example/build files, a dependency bug alone, or best-practice cleanup without exploitable impact.
- Focus on real compromise paths: session-token/HMAC/signature forgery, OAuth CSRF or access-token theft, cross-tenant session access, injection into session storage, open redirect/SSRF, secret disclosure, and DoS in an authentication handler.

## Validate
- Trace the exact reachable path from attacker input (HTTP request, webhook/app-proxy payload, forged JWT, OAuth callback params) into the affected function.
- Check whether existing checks already stop it: HMAC verification in `validateHmac`/`validateHmacFromRequestFactory`, `safeCompare`, JWT claim checks in `decodeSessionToken`, the OAuth nonce/cookie in the begin/callback flow, `sanitizeShop`/`sanitizeHost`/`validateRedirectUrl`, and session-storage parameterization.
- Account for what the attacker actually controls versus what is signed by the app secret they do not have.
- Accept only concrete impact: forged authenticated session, accepted forged webhook/proxy request, stolen or injected access token, cross-tenant data/state access, open redirect/SSRF with a real consequence, secret leak, or a crash/hang of an auth handler.
- Require exact file/function support and a reproducible Jest test or request-level PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and matching Shopify bounty impact class]

### Likelihood Explanation
[Preconditions, attacker capability, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Jest test, crafted HTTP/webhook/app-proxy request, or forged JWT/HMAC sequence with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for shopify-app-js security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject privileged-actor (app developer, Shopify staff, operator), leaked-secret/`apiSecretKey`, physical/local-network, MITM, social-engineering, dependency-only, docs/style, and test/mock/config-only issues.
- Reject reflected plain-text injection, self-XSS, logout/no-impact CSRF, missing-security-header claims, and pure-DDoS claims.
- Reject self-harm, best-practice critique, scanner output, and theoretical claims with no demonstrated impact.
- A valid report must be triggerable by an anonymous HTTP/webhook/app-proxy client or a single merchant/customer against an app running this library on default configuration.
- The final impact must map to an in-scope class: forged authenticated session or accepted forged Shopify request, OAuth CSRF / access-token theft, cross-tenant session/data access, session-storage injection, open redirect/SSRF, secret disclosure, or DoS of an authentication handler.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions -> attacker request/payload -> trigger -> bad result.
4. Existing HMAC/JWT/nonce/redirect/storage checks reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood and attacker capability.
6. Reproducible proof path: Jest PoC, crafted HTTP/webhook request, or forged JWT/HMAC sequence.
7. No obvious rejection reason from SECURITY.md, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an anonymous client or a single merchant/customer trigger this with no privileged role and no `apiSecretKey`/leaked secret?
- Does the code actually behave as claimed on the current release version and default config?
- Is the impact caused by this library's code, not by the host app or a dependency alone?
- Is the forged session, accepted forged request, token theft, cross-tenant access, redirect/SSRF, or crash concrete and not hypothetical?
- Would a Shopify triager accept the proof?
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
[Concrete in-scope impact, severity rationale, and Shopify bounty category]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible request/JWT/HMAC sequence or Jest test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for shopify-app-js.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged analogs in session-token/HMAC/signature verification, OAuth nonce/cookie/token handling, shop/host/redirect sanitization, session storage/lookup, request-authentication handlers, or outbound API URL/credential construction.
- Reject privileged-actor, leaked-secret, MITM, dependency-only, test-only, and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable shopify-app-js path from an anonymous HTTP/webhook/app-proxy request or a single merchant/customer.
- Prove root cause with exact file/module/function support.
- Accept only concrete forged session, accepted forged Shopify request, OAuth CSRF/token theft, cross-tenant access, session-storage injection, open redirect/SSRF, secret disclosure, or DoS of an auth handler.

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
