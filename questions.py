import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 23
# todo: the path from https://github.com/chromium/chromium
SOURCE_REPO = "chromium/chromium"
# todo: the name of the repository
REPO_NAME = "chromium"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


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
    # Site isolation & process model: origin locks the browser must enforce on a renderer
    # =================================================================================
    "content/browser/site_instance_impl.cc",
    "content/browser/browsing_instance.cc",
    "content/browser/process_lock.cc",
    "content/browser/site_info.cc",
    "content/browser/site_instance_group.cc",
    "content/browser/isolated_origin_util.cc",
    "content/browser/origin_agent_cluster_isolation_state.cc",
    "content/browser/security/cpsp/child_process_security_policy_impl.cc",
    "content/browser/storage_partition_impl.cc",
    "content/browser/renderer_host/render_process_host_impl.cc",
    "content/browser/renderer_host/render_frame_host_impl.cc",
    "content/browser/renderer_host/agent_scheduling_group_host.cc",

    # =================================================================================
    # Navigation & commit: origin/URL a renderer is allowed to commit, cross-origin swaps
    # =================================================================================
    "content/browser/renderer_host/navigation_request.cc",
    "content/browser/renderer_host/navigator.cc",
    "content/browser/renderer_host/navigation_controller_impl.cc",
    "content/browser/renderer_host/frame_tree_node.cc",
    "content/browser/renderer_host/render_frame_host_manager.cc",
    "content/browser/renderer_host/render_frame_proxy_host.cc",
    "content/browser/renderer_host/mixed_content_navigation_throttle.cc",
    "content/browser/renderer_host/render_frame_host_csp_context.cc",

    # =================================================================================
    # Mojo IPC boundary: validation of renderer-supplied messages/handles (sandbox escape)
    # =================================================================================
    "mojo/core/channel.cc",
    "mojo/core/broker_host.cc",
    "mojo/core/ipcz_driver/mojo_message.cc",
    "mojo/core/ipcz_driver/shared_buffer.cc",
    "mojo/core/ipcz_driver/shared_buffer_mapping.cc",
    "mojo/core/ipcz_driver/data_pipe.cc",
    "mojo/public/cpp/bindings/lib/validation_context.cc",
    "mojo/public/cpp/bindings/lib/validation_util.cc",
    "mojo/public/cpp/bindings/lib/message.cc",
    "mojo/public/cpp/bindings/lib/multiplex_router.cc",
    "mojo/public/cpp/bindings/lib/interface_endpoint_client.cc",
    "mojo/public/cpp/bindings/lib/connector.cc",

    # =================================================================================
    # URL & origin computation: parsing/canonicalization the SOP and origin locks rely on
    # =================================================================================
    "url/gurl.cc",
    "url/origin.cc",
    "url/scheme_host_port.cc",
    "url/url_canon_host.cc",
    "url/url_canon_etc.cc",
    "url/url_canon_path.cc",
    "url/url_util.cc",

    # =================================================================================
    # Blink HTML/CSS parsing & DOM lifecycle: renderer memory corruption from markup
    # =================================================================================
    "third_party/blink/renderer/core/html/parser/html_document_parser.cc",
    "third_party/blink/renderer/core/html/parser/html_tokenizer.cc",
    "third_party/blink/renderer/core/html/parser/html_tree_builder.cc",
    "third_party/blink/renderer/core/html/parser/html_construction_site.cc",
    "third_party/blink/renderer/core/html/parser/html_document_parser_fastpath.cc",
    "third_party/blink/renderer/core/html/parser/text_resource_decoder.cc",
    "third_party/blink/renderer/core/css/parser/css_tokenizer.cc",
    "third_party/blink/renderer/core/css/parser/css_parser_fast_paths.cc",
    "third_party/blink/renderer/core/dom/container_node.cc",
    "third_party/blink/renderer/core/dom/document.cc",
    "third_party/blink/renderer/core/dom/element.cc",
    "third_party/blink/renderer/core/dom/node.cc",
    "third_party/blink/renderer/core/dom/range.cc",
    "third_party/blink/renderer/core/dom/shadow_root.cc",
    "third_party/blink/renderer/core/editing/editing_utilities.cc",
    "third_party/blink/renderer/core/editing/frame_selection.cc",
    "third_party/blink/renderer/core/layout/layout_object.cc",
    "third_party/blink/renderer/core/layout/layout_block_flow.cc",

    # =================================================================================
    # V8<->Blink bindings & structured clone: type confusion / OOB from script input
    # =================================================================================
    "third_party/blink/renderer/bindings/core/v8/serialization/serialized_script_value.cc",
    "third_party/blink/renderer/bindings/core/v8/serialization/v8_script_value_deserializer.cc",
    "third_party/blink/renderer/bindings/core/v8/serialization/v8_script_value_serializer.cc",
    "third_party/blink/renderer/bindings/core/v8/serialization/trailer_reader.cc",
    "third_party/blink/renderer/bindings/core/v8/serialization/post_message_helper.cc",
    "third_party/blink/renderer/bindings/core/v8/v8_script_runner.cc",
    "third_party/blink/renderer/bindings/core/v8/script_controller.cc",
    "third_party/blink/renderer/bindings/core/v8/v8_binding_for_core.cc",

    # =================================================================================
    # Blink loader/fetch: response-origin confusion, CORS/ORB enforcement in the renderer
    # =================================================================================
    "third_party/blink/renderer/platform/loader/fetch/resource_loader.cc",
    "third_party/blink/renderer/platform/loader/fetch/resource.cc",
    "third_party/blink/renderer/platform/loader/fetch/resource_response.cc",
    "third_party/blink/renderer/platform/loader/fetch/response_body_loader.cc",

    # =================================================================================
    # Network service: CORS, ORB, cookies, URLLoader - the cross-origin read boundary
    # =================================================================================
    "services/network/cors/cors_url_loader.cc",
    "services/network/cors/cors_url_loader_factory.cc",
    "services/network/cors/preflight_controller.cc",
    "services/network/cors/preflight_result.cc",
    "services/network/cors/preflight_cache.cc",
    "services/network/url_loader.cc",
    "services/network/cookie_manager.cc",
    "services/network/cookie_settings.cc",
    "services/network/network_context.cc",
    "services/network/public/cpp/orb/orb_impl.cc",
    "services/network/public/cpp/orb/orb_sniffers.cc",

    # =================================================================================
    # Network wire parsing: HTTP/WebSocket bytes from attacker-controlled servers
    # =================================================================================
    "net/http/http_stream_parser.cc",
    "net/http/http_response_headers.cc",
    "net/http/http_chunked_decoder.cc",
    "net/websockets/websocket_frame_parser.cc",
    "net/websockets/websocket_frame.cc",
    "net/websockets/websocket_deflate_stream.cc",
    "net/websockets/websocket_basic_stream.cc",

    # =================================================================================
    # Storage endpoints reachable from a renderer: Blob and IndexedDB Mojo surfaces
    # =================================================================================
    "storage/browser/blob/blob_registry_impl.cc",
    "storage/browser/blob/blob_url_store_impl.cc",
    "storage/browser/blob/blob_storage_context.cc",
    "storage/browser/blob/blob_reader.cc",
    "content/browser/indexed_db/instance/connection.cc",
    "content/browser/indexed_db/instance/database.cc",

    # =================================================================================
    # GPU command buffer: GPU-process memory corruption driven by WebGL/renderer commands
    # =================================================================================
    "gpu/command_buffer/service/gles2_cmd_decoder.cc",
    "gpu/command_buffer/service/buffer_manager.cc",
    "gpu/command_buffer/service/texture_manager.cc",
    "gpu/command_buffer/service/framebuffer_manager.cc",
    "gpu/command_buffer/service/command_buffer_service.cc",
    "gpu/command_buffer/common/gles2_cmd_utils.cc",

    # =================================================================================
    # Media demuxers/parsers: memory corruption from crafted audio/video bytes
    # =================================================================================
    "media/formats/mp4/box_reader.cc",
    "media/formats/mp4/mp4_stream_parser.cc",
    "media/formats/mp4/track_run_iterator.cc",
    "media/formats/mp4/avc.cc",
    "media/formats/mp4/hevc.cc",
    "media/formats/webm/webm_cluster_parser.cc",
    "media/formats/webm/webm_stream_parser.cc",
    "media/formats/webm/webm_parser.cc",
]


target_scopes = [
    "Critical. A compromised or malicious renderer escapes the sandbox because a browser-process Mojo endpoint trusts renderer-supplied data: ChildProcessSecurityPolicyImpl::CanAccessDataForOrigin / CanCommitOriginAndUrl, ProcessLock, RenderFrameHostImpl or AgentSchedulingGroupHost accept an origin, URL, frame token, or handle the calling renderer is not locked to, letting the renderer read/write another site's data, forge a message from a frame it does not own, or reach a browser capability outside its sandbox.",
    "Critical. The same-origin policy is bypassed (universal XSS / cross-origin theft) because SiteInstanceImpl, BrowsingInstance, RenderFrameHostManager, RenderFrameProxyHost, or NavigationRequest place a document in the wrong SiteInstance/process, reuse a process across a security boundary, or commit a document with an origin that does not match its true source, so script from the attacker's page reads or scripts a cross-origin document.",
    "Critical. An origin or URL is computed ambiguously in url/ (GURL, Origin, SchemeHostPort, host/path canonicalization) so two different inputs resolve to the same origin or one origin is mistaken for another; a renderer or the network service then applies an origin lock, CORS decision, or cookie scope to the wrong origin, yielding cross-origin data access or an origin-confusion spoof.",
    "Critical. A renderer-process memory-safety bug (OOB read/write, use-after-free, type confusion, uninitialized read) is reached from attacker markup or script through the Blink HTML/CSS parser, DOM tree mutation, editing/selection, or layout code, giving control of renderer memory from a page the victim merely visits - the first half of an RCE chain.",
    "Critical. V8<->Blink bindings or structured clone corrupt memory or confuse types because V8ScriptValueDeserializer / SerializedScriptValue mis-handle an attacker-crafted serialized blob delivered via postMessage, IndexedDB, or history.state: a bad tag, length, transferred handle, or object reference produces an OOB read/write or a wrong-type object in the renderer.",
    "Critical/High. The cross-origin read boundary in the network service is defeated because CorsURLLoader, PreflightController/PreflightResult/PreflightCache, or Opaque Response Blocking (orb_impl / orb_sniffers) admits a response it must block or skips a required preflight, letting the attacker's page read a cross-origin resource's bytes (cross-origin information disclosure).",
    "Critical. The GPU process is memory-corrupted from WebGL/WebGPU-issued command-buffer traffic because GLES2DecoderImpl or the buffer/texture/framebuffer managers under-validate sizes, offsets, or object references in commands a renderer submits, producing OOB access or a use-after-free in a process that survives renderer restart.",
    "Critical. A media demuxer corrupts memory from crafted bytes a page feeds through <video>/<audio>/MSE, because BoxReader, Mp4StreamParser, TrackRunIterator, the AVC/HEVC bitstream converters, or the WebM cluster/stream parsers mis-size a buffer, trust an attacker length/count, or read past a box/block boundary, giving an OOB read/write in the media pipeline.",
    "High. A renderer reads or writes storage across an origin or storage-partition boundary because a Blob (BlobRegistryImpl, BlobURLStoreImpl) or IndexedDB (Connection, Database) Mojo endpoint binds the wrong origin/partition, resolves a blob URL registered by another origin, or frees an object still referenced, leaking cross-origin data or corrupting browser-process memory.",
    "Critical/High blind spot. Remote web content or an untrusted renderer abuses an assumption Chromium never wrote down: a value validated in one process trusted as validated in another, an origin/URL re-derived after the check that authorized it, a rule enforced on one navigation/response/message path but not its redirect, prerender, fenced-frame, blob-URL, or synthetic-response twin, a handle or pointer whose lifetime is proven safe only inside a single call, or a Mojo interface reachable before the frame's origin lock is applied - yielding a sandbox escape, same-origin-policy bypass, cross-origin disclosure, or attacker-controlled memory corruption.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one chromium target.

    ```
    target_file format:
    "'File Name: services/network/cors/cors_url_loader.cc -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact chromium target:

    {target_file}

    Project focus:
    chromium is the browser behind Chrome. Focus only on what remote web content or an untrusted renderer reaches: the site-isolation/process model and origin locks, navigation and origin/URL commit, the renderer<->browser Mojo IPC boundary, URL and origin parsing, Blink HTML/CSS parsing and DOM/layout, V8<->Blink bindings and structured clone, the loader/fetch pipeline, the network service's CORS/ORB/cookie boundary, HTTP/WebSocket wire parsing, Blob/IndexedDB storage endpoints, the GPU command buffer, and media demuxers.

    Rules:
    * Treat `File Name:` as the exact file/component.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact C++ symbols (function, method, class, field, Mojo interface) when possible.
    * Attacker is unprivileged only: remote web content the victim merely visits (HTML, CSS, JS, WASM, media, fonts, images, responses from the attacker's own servers, WebGL/WebGPU calls, and any Mojo IPC script can issue), and - per Chrome's documented threat model - a compromised or fully malicious renderer sending arbitrary Mojo IPC across the sandbox boundary.
    * Attacker is NOT a local user, does NOT have OS/host/physical access, non-default flags or enterprise policies, an installed extension, an MITM/network position, or the victim's cooperation beyond loading a page and at most one click.
    * Out of scope, never ask about: bugs needing command-line flags, enterprise policy, or field trials; extensions, apps, or the WebUI/settings surface; MITM/TLS-only issues; local/physical access; social engineering; third-party code not built as Chromium; fingerprinting; and pure denial-of-service or crash-only-without-memory-safety (an unexploitable null-deref or resource-exhaustion tab crash is out of scope).
    * Ignore test files, mocks, fuzzers, benchmarks, docs, generated code (`*.mojom.cc`, `*-inl.h`, bindings glue), and build/config-only findings.
    * Every question must describe a real page, script call, media/response payload, GPU command, or Mojo message an attacker actually delivers through a valid entrypoint, and a concrete broken invariant. No generic "what if the input is huge" without a submitted payload and a corrupted object or crossed boundary.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target sandbox escape, same-origin-policy bypass / universal XSS, cross-origin information disclosure, or attacker-controlled memory corruption (OOB read/write, use-after-free, type confusion, uninitialized read) in the renderer, GPU, network, or browser process.
    * Every question must be testable by a C++ unit or browser test: a content/ browsertest, a Blink web/unit test, a Mojo interface test, a network-service test, a GPU decoder test, or a media parser test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Process isolation: the browser process never trusts renderer-supplied data; a renderer only accesses data for origins its ProcessLock allows (CanAccessDataForOrigin / CanCommitOriginAndUrl).
    * Same-origin policy: content from one origin cannot read or script another origin's document or bytes unless CORS/postMessage explicitly permits it.
    * Origin integrity: the origin computed for a document or response equals its true source; URL and origin parsing is unambiguous.
    * Memory safety: no attacker-controlled input produces an OOB access, use-after-free, type confusion, or uninitialized read in any process.
    * Sandbox integrity: a compromised renderer cannot gain code execution or reach resources outside its sandbox through a Mojo interface.

    Each question must include:
    1. target function/method;
    2. attacker action (a concrete page, script/DOM call, media or network payload, GPU command, or Mojo message: interface, method, fields, arguments);
    3. preconditions (frames, origins, process lock, contracts, handles the attacker controls);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: C++ content-browsertest/Blink/Mojo/network/GPU/media test PARAMETERS and assert PROCESS_ISOLATION, SAME_ORIGIN_POLICY, ORIGIN_INTEGRITY, MEMORY_SAFETY, or SANDBOX_INTEGRITY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused chromium exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: remote web content the victim visits (HTML/CSS/JS/WASM, media, fonts, responses from the attacker's servers, WebGL/WebGPU calls, and Mojo IPC script can issue), or - per Chrome's threat model - a compromised/malicious renderer sending arbitrary Mojo IPC. No OS/host/physical access, non-default flags or policies, extensions, MITM position, or victim cooperation beyond loading a page and at most one click.
- Reject paths needing command-line flags, enterprise policy, field trials, extensions/apps, WebUI, MITM/TLS-only, local access, social engineering, or third-party code not built as Chromium.
- Reject fingerprinting, missing-hardening, best-practice, and pure denial-of-service or crash-only-without-memory-safety findings (an unexploitable null-deref or resource-exhaustion tab crash is out of scope).
- Reject test/mock/fuzzer/docs/generated/build-config-only findings.
- Reject generic resource-growth claims with no concrete submitted payload and no crossed boundary or corrupted object.
- Chrome VRP rewards Critical, High and Medium. Focus on real security impact: sandbox escape, remote code execution, same-origin-policy bypass / universal XSS, cross-origin information disclosure, attacker-controlled memory corruption (OOB read/write, use-after-free, type confusion), or a convincing security-UI/origin spoof.

## Validate
- Trace the exact reachable path from the attacker's page, script/DOM call, media or network payload, GPU command, or Mojo message into the affected function.
- Check whether existing checks already stop it: the process-lock and CanAccessDataForOrigin/CanCommitOriginAndUrl checks, navigation origin/URL validation, Mojo message validation, CORS/ORB and preflight enforcement, or command-buffer/parser bounds checks.
- Confirm the path is reachable in a default-configuration release build with site isolation on, no special flags.
- Accept only a concrete sandbox escape, SOP bypass, cross-origin disclosure, memory-corruption primitive, or security-UI spoof - not an unexploitable crash.
- Require exact file/function support and a reproducible C++ content-browsertest, Blink, Mojo, network-service, GPU-decoder, or media-parser proof.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (sandbox escape / RCE, or SOP bypass giving broad cross-origin control), High (cross-origin data disclosure, renderer memory corruption, or a universal-XSS primitive), or Medium (narrow info leak, deterministic origin-confusion spoof, or a constrained memory-safety issue)]

### Likelihood Explanation
[Preconditions, frames/origins/process state needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[C++ content-browsertest/Blink/Mojo/network/GPU/media test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for chromium.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs remote web content or an untrusted renderer can reach: the site-isolation/process model and origin locks, navigation/origin commit, the renderer<->browser Mojo IPC boundary, URL/origin parsing, Blink HTML/CSS/DOM/layout, V8<->Blink bindings and structured clone, the loader/fetch pipeline, the network service's CORS/ORB/cookie boundary, HTTP/WebSocket parsing, Blob/IndexedDB endpoints, the GPU command buffer, or media demuxers.
- Reject paths needing flags, enterprise policy, extensions, WebUI, MITM/TLS-only, local/physical access, social engineering, or third-party code not built as Chromium.
- Reject fingerprinting, best-practice, mocked-only paths, and pure denial-of-service or crash-only-without-memory-safety analogs.
- Medium, High and Critical only; no low, informational, or resource-only analogs.

## Validate
- Map the bug class to the strongest reachable chromium path from a single page, script/DOM call, media or network payload, GPU command, or Mojo message.
- Prove root cause with exact file/function support.
- Accept only a concrete sandbox escape, SOP bypass / universal XSS, cross-origin disclosure, attacker-controlled memory corruption, or convincing security-UI spoof.

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


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for chromium security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes, and apply the Chrome VRP severity model.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Chrome VRP rewards Critical, High and Medium; reject low, informational, best-practice, hardening, and speculative reports.
- Reject paths needing command-line flags, enterprise policy, field trials, an installed extension or app, WebUI/settings, an MITM/network position, local or physical access, victim social engineering, or the victim's cooperation beyond loading a page and at most one click.
- Reject fingerprinting, SSL/TLS best-practice, missing-header, and third-party (not-built-as-Chromium) findings.
- Reject pure denial-of-service and crash-only-without-memory-safety reports (an unexploitable null-deref or resource-exhaustion tab crash is out of scope); a crash is in scope only when it is a controllable memory-safety bug.
- Reject docs/style, generated-file, and test/mock/build-config-only issues.
- A valid report must be triggerable by remote web content or an untrusted renderer, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope category: Critical - sandbox escape or remote code execution reachable from a web page, or a same-origin-policy bypass giving broad cross-origin read/write; High - cross-origin information disclosure, attacker-controlled memory corruption (OOB read/write, use-after-free, type confusion) in the renderer, GPU, network, or browser process, or a universal-XSS primitive; Medium - a narrow information leak, a deterministic origin-confusion or security-UI spoof, or a constrained memory-safety issue with limited attacker control.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and a broken process-isolation, same-origin-policy, origin-integrity, memory-safety, or sandbox-integrity invariant.
3. Reachable exploit path: preconditions (attacker-controlled frames, origins, process state, handles) -> page/script/DOM call, media or network payload, GPU command, or Mojo message -> trigger -> bad result.
4. Existing checks reviewed and shown insufficient: process lock and CanAccessDataForOrigin/CanCommitOriginAndUrl, navigation origin/URL validation, Mojo message validation, CORS/ORB and preflight, and command-buffer/parser bounds checks.
5. Concrete in-scope Critical/High/Medium impact with realistic likelihood.
6. Reproducible proof path: a C++ content-browsertest, Blink web/unit test, Mojo interface test, network-service test, GPU-decoder test, media-parser test, or exact steps in a default release build.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can remote web content or an untrusted renderer trigger this without flags, policy, extensions, MITM, local access, or victim cooperation beyond a page load?
- Does the code actually behave as claimed in a default release build with site isolation on?
- Is the impact caused by this code, not by a third-party component or a mere unexploitable crash?
- Is the escape, SOP bypass, disclosure, corruption, or spoof concrete rather than hypothetical?
- Would a Chrome VRP / Security Sheriff triager accept the proof-of-concept?
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
[Concrete in-scope impact, severity rationale, and Chrome VRP impact category]

## Likelihood Explanation
[Attacker capability, frames/origins/process state required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or C++ content-browsertest/Blink/Mojo/network/GPU/media test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
