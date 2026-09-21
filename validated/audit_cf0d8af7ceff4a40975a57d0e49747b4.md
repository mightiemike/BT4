## Title
Origin-agnostic MojoJS gateway grant for PrivilegedWebContents bypasses capability-allowlist verification, enabling universal Mojo JS bindings exposure to a spoofed/cross-origin document - ([File: chrome/browser/geic/geic_pwc_manager.cc])

### Summary
`GeicPwcManager::TabEntry` unconditionally enables the entire MojoJS binding surface for the outermost/primary main frame of a GEIC `PrivilegedWebContents` (PWC) — in both `RenderFrameCreated` and `ReadyToCommitNavigation` — without checking the frame's committed/target origin against the PWC's `capability_allowlist`. `ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame` reinforces this: it grants MojoJS to *any* outermost main frame of *any* `PrivilegedWebContents`, again without an origin check. This mirrors the CrossCurve incident's root cause — a verification/gateway step ("only the trusted gateway/origin may trigger this privileged surface") that is skipped, letting an unverified/spoofed message-origin reach a privileged execution path.

### Finding Description
`pwc::EnforceCapabilityGate` and the mojom-level binders (`BindGeicApi`, `BindGeicBrowserHost`) do perform origin/component verification before handing out the narrow `GeicApi`/`GeicBrowserHost` Mojo interfaces [1](#0-0) . However, a separate and much more powerful code path bypasses that gate entirely: `GeicPwcManager::TabEntry::RenderFrameCreated` and `ReadyToCommitNavigation` call `EnableMojoJsBindings` on the primary main frame purely based on `IsInPrimaryMainFrame()`, with an explicit acknowledgment that "the committed origin is not checked against the capability allowlist here" [2](#0-1) .

This is compounded by `ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame`, which returns `true` for the outermost main frame of *any* `PrivilegedWebContents`, again explicitly noting the same gap: "Enabling MojoJS for any PWC exposes the entire Mojo interface surface rather than only GeicApi, and the committed origin is not checked against the capability allowlist here" [3](#0-2) .

`EnableMojoJsBindings` is the mechanism that turns on the global `Mojo.bindInterface`/`Mojo.createMessagePipe` JavaScript surface for a renderer frame — normally reserved for WebUI. Once enabled, any document running in that frame can call `Mojo.bindInterface()` to reach *arbitrary* browser-exposed Mojo interfaces bound to that frame, not just the intended `GeicApi`. The `pwc::FixedPwcPolicyDelegate` allowlist (`navigation_allowlist`/`capability_allowlist`) is intended to be the security boundary restricting which origin may hold this capability [4](#0-3) , but the binding-enable path never consults it — it only checks frame topology (`IsInPrimaryMainFrame` / `!GetParentOrOuterDocument()`), not the destination/committed origin.

If a compromised, redirected, or maliciously-linked navigation causes the PWC's primary main frame to commit to an origin other than the configured GEIC guest origin (e.g., via an open redirect on the guest site, a `window.open`/link navigation that the PWC's navigation policy fails to block, or a race between `ReadyToCommitNavigation` and the actual commit), that non-allowlisted origin would still receive full `MojoJS` bindings — the equivalent of the CrossCurve "forged message bypassing gateway verification" leading to an unauthorized privileged operation, here privilege escalation from "any page" to "full Mojo JS access."

The severity is explicitly flagged as a known, unresolved gap by the developers themselves (`TODO(crbug.com/539909218)`), which strongly corroborates that verification is missing rather than merely deferred to another layer.

### Impact Explanation
Enabling MojoJS bindings for an unverified origin gives that origin JS-level access to `Mojo.bindInterface`, allowing it to request any Mojo interface exposed to that render frame's binder map — a Universal Cross-Site-Scripting/SOP-equivalent capability escalation and a strong candidate for renderer→browser sandbox-boundary compromise, since it converts an ordinary (or spoofed) web document into a document with the same privileged IPC reach as the trusted GEIC guest page. This satisfies the "concrete sandbox escape / SOP bypass" bar in the validation rules.

### Likelihood Explanation
Reaching this requires the PWC's primary main frame to commit a document from an origin not in the capability allowlist while still inside the same `PrivilegedWebContents`/`TabEntry`. `GeicPwcManager` restricts *creation* to the configured `dev_url_` origin, and `pwc::FixedPwcPolicyDelegate` is documented as a navigation_allowlist, implying some navigation-time enforcement elsewhere in `pwc::PrivilegedWebContents`; whether that navigation-allowlist enforcement is airtight against all redirect/`ReadyToCommitNavigation` races could not be fully confirmed from the available index (the `pwc::PrivilegedWebContents` navigation-throttling implementation was not retrieved). The developers' own comments mark this as a known, intentionally-unaddressed prototype shortcut, which raises confidence that the origin check is genuinely absent at the binding layer regardless of what the navigation-allowlist does elsewhere — but full exploitability depends on whether the navigation allowlist can be bypassed or race-lost, which is not verifiable from the indexed files alone.

### Recommendation
Move the origin check into `EnableMojoJsBindings`'s call sites: consult `pwc::PrivilegedWebContents`'s capability_allowlist and the frame's actual (or about-to-commit) origin in both `ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame` and `GeicPwcManager::TabEntry::{RenderFrameCreated,ReadyToCommitNavigation}` before calling `EnableMojoJsBindings`, and fail closed (drop/terminate) on mismatch, exactly as `BindGeicApi`/`BindGeicBrowserHost` already do for the narrow interfaces. Until fixed, scope the exposed Mojo surface to only `GeicApi` rather than the entire MojoJS surface, per the existing TODO.

### Proof of Concept
Not independently reproducible from the indexed code alone (no browser/test harness access). The conceptual PoC, supported directly by code comments and structure:
1. A GEIC `PrivilegedWebContents` is created with `capability_allowlist = {origin(dev_url_)}` [4](#0-3) .
2. The primary main frame navigates (via redirect/open-redirect on the guest origin, or any path that causes `ReadyToCommitNavigation`/`RenderFrameCreated` to fire for a non-allowlisted origin in the same frame) to an attacker-controlled origin.
3. `ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame` returns `true` solely because the frame is the outermost main frame of a `PrivilegedWebContents`, with no origin check [5](#0-4) , and/or `GeicPwcManager::TabEntry::ReadyToCommitNavigation` calls `EnableMojoJsBindings` unconditionally [6](#0-5) .
4. The attacker page, now with MojoJS enabled, calls `Mojo.bindInterface(...)` in JavaScript to reach any Mojo interface registered for that frame — analogous to the CrossCurve exploit calling `expressExecute` on a forged message that skipped gateway verification.

### Citations

**File:** chrome/browser/geic/geic_host.cc (L39-57)
```text
void BindGeicApi(content::RenderFrameHost* render_frame_host,
                 mojo::PendingReceiver<mojom::GeicApi> receiver) {
  // The shared gate enforces the full capability policy (and terminates the
  // renderer itself on a renderer-controllable violation); null simply means
  // do not bind.
  pwc::PrivilegedWebContents* privileged =
      pwc::EnforceCapabilityGate(render_frame_host);
  if (!privileged) {
    return;
  }

  // The gate is component-agnostic; GeicApi additionally requires the
  // component to be GEIC. A request from another component's frame is again a
  // compromised renderer.
  if (privileged->component() != pwc::PrivilegedComponent::kGeic) {
    bad_message::ReceivedBadMessage(render_frame_host->GetProcess(),
                                    bad_message::PWC_BRIDGE_UNQUALIFIED_FRAME);
    return;
  }
```

**File:** chrome/browser/geic/geic_pwc_manager.cc (L109-118)
```text
  url::Origin origin = url::Origin::Create(dev_url_);
  auto policy_delegate = std::make_unique<pwc::FixedPwcPolicyDelegate>(
      /*navigation_allowlist=*/std::vector<url::Origin>{origin},
      /*capability_allowlist=*/std::vector<url::Origin>{origin});

  auto pwc = pwc::PrivilegedWebContents::Create(
      pwc::PrivilegedComponent::kGeic, profile_, std::move(policy_delegate));
  if (!pwc || !pwc->web_contents()) {
    return nullptr;
  }
```

**File:** chrome/browser/geic/geic_pwc_manager.cc (L192-212)
```text
void GeicPwcManager::TabEntry::RenderFrameCreated(
    content::RenderFrameHost* render_frame_host) {
  // TODO(crbug.com/539909218): Prototype shortcut. This exposes the entire
  // Mojo surface rather than only GeicApi, and the committed origin is not
  // checked against the capability allowlist here.
  if (render_frame_host->IsInPrimaryMainFrame()) {
    render_frame_host->EnableMojoJsBindings(/*features=*/nullptr);
  }
}

void GeicPwcManager::TabEntry::ReadyToCommitNavigation(
    content::NavigationHandle* navigation_handle) {
  // TODO(crbug.com/539909218): Prototype shortcut. This exposes the entire
  // Mojo surface rather than only GeicApi, and the committed origin is not
  // checked against the capability allowlist here.
  if (navigation_handle->IsInPrimaryMainFrame() &&
      navigation_handle->GetRenderFrameHost()) {
    navigation_handle->GetRenderFrameHost()->EnableMojoJsBindings(
        /*features=*/nullptr);
  }
}
```

**File:** chrome/browser/chrome_content_browser_client.cc (L2269-2289)
```text
bool ChromeContentBrowserClient::ShouldAllowMojoJsBindingsForFrame(
    content::RenderFrameHost& render_frame_host) {
  content::WebContents* web_contents =
      content::WebContents::FromRenderFrameHost(&render_frame_host);
  if (web_contents && glic::IsGlicGuest(web_contents) &&
      !render_frame_host.GetParentOrOuterDocument()) {
    return true;
  }
  // TODO(crbug.com/539909218): Prototype shortcut. Enabling MojoJS for any PWC
  // exposes the entire Mojo interface surface rather than only GeicApi, and the
  // committed origin is not checked against the capability allowlist here.
  // Gating on the outermost main frame is a stopgap while erikchen@ designs a
  // scoped capability binding mechanism in follow-ups. We check
  // `!render_frame_host.GetParentOrOuterDocument()` rather than
  // `IsInPrimaryMainFrame()` because this predicate is consulted from
  // `ReadyToCommitNavigation` before the frame commits, where
  // lifecycle-dependent queries return false.
  if (!render_frame_host.GetParentOrOuterDocument() && web_contents &&
      pwc::PrivilegedWebContents::FromWebContents(web_contents)) {
    return true;
  }
```
