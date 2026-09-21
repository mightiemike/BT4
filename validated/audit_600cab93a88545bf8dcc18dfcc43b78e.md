### Title
GEIC PrivilegedWebContents grants full MojoJS bindings to the primary main frame without verifying the committed origin against the capability allowlist - (File: chrome/browser/geic/geic_pwc_manager.cc)

### Summary
The EnsoWallet report describes an `executeShortcut`/`DELEGATECALL` path that grants a caller-supplied library full write access to contract storage because there is no whitelist check on the delegatecall target. The structural analog in this Chromium checkout is `GeicPwcManager::TabEntry` unconditionally calling `RenderFrameHost::EnableMojoJsBindings()` on the primary main frame of a GEIC `PrivilegedWebContents` (PWC), which hands the committed document the entire Mojo/`MojoJS` interface surface registered for that frame — not scoped to the single intended `GeicApi` interface and not gated on the frame's committed origin actually being on the capability allowlist.

### Finding Description
`GeicPwcManager::TabEntry::RenderFrameCreated` and `GeicPwcManager::TabEntry::ReadyToCommitNavigation` both call: [1](#0-0) 

```
void GeicPwcManager::TabEntry::RenderFrameCreated(...) {
  // TODO(crbug.com/539909218): Prototype shortcut. This exposes the entire
  // Mojo surface rather than only GeicApi, and the committed origin is not
  // checked against the capability allowlist here.
  if (render_frame_host->IsInPrimaryMainFrame()) {
    render_frame_host->EnableMojoJsBindings(/*features=*/nullptr);
  }
}
```
and the equivalent in `ReadyToCommitNavigation`, again gated only on "is primary main frame," with the same acknowledged gap. This is the direct analog of the `EXECUTOR`/`delegatecall`-to-unwhitelisted-library issue: a privileged, wide-scope capability (`EnableMojoJsBindings`, which lets the document's JavaScript directly invoke any Mojo interface registered in the frame's binder map, i.e. `blink::mojom::MojoJs` bindings) is granted unconditionally to whatever document ends up committed in that frame, rather than only to the intended, narrowly-scoped `GeicApi` interface after verifying the committed origin.

By contrast, the sibling Glic implementation performs the origin check before granting the same capability: [2](#0-1) 

```
void GlicGuestObserver::MaybeEnableMojoJsBindings(...) {
  if (!navigation_handle->IsInPrimaryMainFrame()) return;
  if (IsOriginAllowedGlicApi(url::Origin::Create(navigation_handle->GetURL()))) {
    navigation_handle->GetRenderFrameHost()->EnableMojoJsBindings(nullptr);
  }
}
```
This confirms that origin-gating MojoJS bindings is the established, expected security control in this codebase, and that the GEIC path (explicitly flagged by its own `TODO(crbug.com/539909218)` comments) omits it. The `PrivilegedWebContents`/`FixedPwcPolicyDelegate` mechanism separately maintains a "navigation allowlist" and a "capability allowlist" (seen in `GeicPwcManager::GetOrCreateWebContentsForTab`, `chrome/browser/geic/geic_pwc_manager.cc:109-116`), but `EnableMojoJsBindings` is invoked from `RenderFrameCreated`/`ReadyToCommitNavigation` before/without consulting that capability allowlist, so any document that ends up committed in the PWC's primary main frame (e.g., following an open redirect, a link click, a `window.location` navigation performed by compromised or malicious content already inside the allowed-origin page, or any other in-scope navigation reachable from the initially loaded guest URL) receives the full MojoJS-bound interface surface, not just `GeicApi`.

### Impact Explanation
`EnableMojoJsBindings` exposes Blink's `Mojo.bindInterface`/JS-to-Mojo bridge for that document, letting its JavaScript directly call into every Mojo interface exposed to that frame's binder map (`PopulateChromeFrameBinders`), including `pwc::mojom::PrivilegedBridge` when the process `IsPrivileged()`. If an attacker-controlled or cross-origin document is ever able to commit as the primary main frame of a GEIC PWC (via redirect or subsequent navigation not re-validated against the capability allowlist), it inherits privileged Mojo IPC capability rather than being restricted to the intentionally minimal, empty `GeicApi` scaffolding interface. This is a browser-process privilege/capability boundary bypass reachable purely via document navigation, matching the requested class of cross-origin/renderer↔browser Mojo IPC boundary violations.

### Likelihood Explanation
The code path is unconditional on every `RenderFrameCreated`/`ReadyToCommitNavigation` for the PWC's primary main frame — there is no per-navigation origin re-check, and the developers' own `TODO` comments acknowledge the missing allowlist check. The remaining uncertainty is whether, given the current `FixedPwcPolicyDelegate` navigation allowlist enforced elsewhere (e.g., in `PrivilegedWebContents`'s navigation throttling, which was not fully inspectable in this pass), a renderer-initiated navigation to a non-allowlisted origin can actually commit in that frame today. I could not fully trace the navigation-allowlist enforcement code path (`pwc::PrivilegedWebContents` navigation throttle/decision logic) within the available index, so I cannot confirm with certainty whether an out-of-allowlist commit is currently reachable in this build, or whether it is prevented by a separate, unexamined navigation-allowlist gate.

### Recommendation
Mirror the Glic pattern: before calling `EnableMojoJsBindings`, verify `navigation_handle->GetURL()` (or the render frame host's last committed origin) against the PWC's capability allowlist (`PwcPolicyDelegate::IsCapabilityOrigin`), exactly as `GlicGuestObserver::MaybeEnableMojoJsBindings` does via `IsOriginAllowedGlicApi`. Additionally, scope the granted capability to only the intended `GeicApi` interface rather than the entire MojoJS surface, consistent with the "whitelist of permitted libraries/interfaces" recommendation from the original report.

### Proof of Concept
Not independently reproducible from the index alone: reproducing this would require confirming, within the actual PWC navigation-allowlist enforcement code (not fully available via search), that a `RenderFrameHost` can reach `RenderFrameCreated`/`ReadyToCommitNavigation` as the GEIC PWC's primary main frame while displaying a document whose origin is not in the `capability_allowlist` passed to `FixedPwcPolicyDelegate` (e.g., via a redirect from the allowed guest URL to an attacker-controlled origin, or via `window.open`/`window.location` navigation performed by script already running in the allowed guest page). If that navigation is possible, then calling any Mojo interface registered in `PopulateChromeFrameBinders` for a privileged process (e.g. `pwc::mojom::PrivilegedBridge`) from that document's JavaScript via `Mojo.bindInterface` after `EnableMojoJsBindings` would constitute the concrete exploit, analogous to invoking arbitrary storage-writing functions through an unwhitelisted delegatecall target.

### Citations

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

**File:** chrome/browser/glic/host/glic_guest_observer.cc (L87-101)
```text
void GlicGuestObserver::MaybeEnableMojoJsBindings(
    content::NavigationHandle* navigation_handle) {
  if (!navigation_handle->IsInPrimaryMainFrame()) {
    return;
  }
  // Enable MojoJS bindings if the pending navigation is targeting an allowed
  // origin so Blink can initialize the Mojo context during document load.
  // The frame's committed origin is checked in `BindGlicWebClientHandler()`
  // when the page attempts to bind the pipe.
  if (IsOriginAllowedGlicApi(
          url::Origin::Create(navigation_handle->GetURL()))) {
    navigation_handle->GetRenderFrameHost()->EnableMojoJsBindings(
        /*features=*/nullptr);
  }
}
```
