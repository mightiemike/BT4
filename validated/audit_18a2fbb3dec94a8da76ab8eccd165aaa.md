### Title
Stale opener URL displayed in Document Picture-in-Picture header after opener navigation (UI spoofing) - ([File: chrome/android/java/src/org/chromium/chrome/browser/media/document_picture_in_picture_header/DocumentPictureInPictureHeaderMediator.java])

### Summary
`DocumentPictureInPictureHeaderMediator` computes the origin/URL string shown in the Document PiP header only once, at construction time, and never re-computes it when the opener `WebContents` navigates afterward.

### Finding Description
In the constructor, `URL_STRING` is derived from `mOpenerWebContents.getLastCommittedUrl()` at the moment the PiP header is created: [1](#0-0) 

The mediator attaches a `WebContentsObserver` on both `mWebContents` and `mOpenerWebContents`, but the only overridden callback is `didChangeVisibleSecurityState()`, which merely re-runs `updateSecurityIcon()` (lock icon / malicious-content status). There is no `didFinishNavigation`, `didFinishLoad`, or similar observer callback that updates `URL_STRING` or `URL_ELLIPSIZE_BEHAVIOR` when the opener tab navigates to a different origin: [2](#0-1) 

`updateSecurityIcon()` reads `SecurityStateModel.getSecurityLevelForWebContents(mOpenerWebContents)` fresh each time it fires, so the padlock/security icon can update live, but the URL text itself, set via `getUrlString(openerUrl)`/`UrlFormatter.formatUrlForSecurityDisplay`, is frozen to the URL captured at header-construction time: [3](#0-2) 

The codebase does contain careful anti-spoofing logic elsewhere in this same feature area — e.g. `DocumentPictureInPictureActivity.verifyOpenerOrigin()` guards against the opener navigating during the asynchronous activity-launch race, and `PopupCreatorImpl.initializeDocumentPipIntent`/`getOpenerOriginString` captures the origin at request time specifically "to prevent origin spoofing if the opener navigates before the Activity completes its launch": [4](#0-3) [5](#0-4) 

However, none of these mechanisms cover the case where the opener tab navigates cross-origin *after* the PiP window and its header are already up and running. In that steady-state scenario the header keeps showing the origin/URL of whatever page was on screen when the PiP window opened — the header is a persistent, chrome-owned security UI surface that a page can trivially outlive by simply navigating itself (e.g., via `location.href = ...`) once it has opened a document PiP window.

### Impact Explanation
An attacker page can open a Document Picture-in-Picture window (a persistent, always-on-top OS-level window carrying Chrome's own security chrome, including a URL bar and security/lock icon) displaying a trusted-looking URL (e.g. `https://accounts.google.com`), then navigate the opener tab in place to attacker-controlled content while the PiP header continues to display the stale, now-incorrect origin and security state. Because the PiP header is treated by the user as authoritative browser security UI (there's an explicit anti-spoofing head-elision design comment for exactly this reason), this allows convincing UI spoofing of the displayed origin, matching the CVE-2026-3927 bug class (incorrect security UI in PictureInPicture allowing UI spoofing via crafted HTML).

### Likelihood Explanation
Reachable from a single web page using only standard APIs: request Document Picture-in-Picture (`documentPictureInPicture.requestWindow()`), then perform an in-page navigation on the opener document. No flags, extensions, or privileged access are required — only user activation to enter PiP and a click-through for the requestWindow permission prompt (`UI:R` matches the CVSS vector in the report).

### Recommendation
Add a navigation-aware callback (e.g. override `didFinishNavigation`/`primaryMainDocumentElementAvailable` or hook into `NavigationHandle` completion) on the `mOpenerWebContentsObserver` in `DocumentPictureInPictureHeaderMediator` that re-derives `URL_STRING` and `URL_ELLIPSIZE_BEHAVIOR` from the opener's up-to-date `getLastCommittedUrl()`/`getLastCommittedOrigin()`, mirroring what `updateSecurityIcon()` already does for the lock icon, so the header origin text always reflects the opener's current committed origin.

### Proof of Concept
1. Attacker page at `https://accounts.google.com`-lookalike or, ideally, an actually trusted-looking `https://accounts.google.com` origin (if attacker can get there transiently, e.g. via an open redirect) triggers `documentPictureInPicture.requestWindow()` and moves a `<video>`/content into it.
2. PiP header is created; `DocumentPictureInPictureHeaderMediator` sets `URL_STRING` to the trusted opener origin.
3. The opener tab immediately performs `location.replace('https://attacker.example/phishing')`.
4. The PiP window keeps floating on top of all windows, still showing the original trusted origin in its header (`URL_STRING` unchanged), while the underlying opener tab now renders the attacker's phishing content — the user is misled into trusting the phishing page because the always-visible PiP header still claims a trusted origin.

Note: I could not find any additional navigation-observer wiring for `mOpenerWebContentsObserver` elsewhere in the codebase within the available index; this conclusion is based on the full constructor and `destroy()` of `DocumentPictureInPictureHeaderMediator`, which show only the `didChangeVisibleSecurityState` override. [6](#0-5)

### Citations

**File:** chrome/android/java/src/org/chromium/chrome/browser/media/document_picture_in_picture_header/DocumentPictureInPictureHeaderMediator.java (L122-133)
```java
        updateSecurityIcon();
        GURL openerUrl = mOpenerWebContents.getLastCommittedUrl();
        mModel.set(DocumentPictureInPictureHeaderProperties.URL_STRING, getUrlString(openerUrl));
        // To prevent spoofing, standard HTTP/HTTPS URLs with non-empty hostnames are
        // head-elided, matching desktop elision behavior. All other schemes (local,
        // chrome://, etc.) are tail-elided so their scheme prefix remains visible.
        boolean isHttpOrHttps = openerUrl != null && UrlUtilities.isHttpOrHttps(openerUrl);
        mModel.set(
                DocumentPictureInPictureHeaderProperties.URL_ELLIPSIZE_BEHAVIOR,
                (isHttpOrHttps && !openerUrl.getHost().isEmpty())
                        ? TextUtils.TruncateAt.START
                        : TextUtils.TruncateAt.END);
```

**File:** chrome/android/java/src/org/chromium/chrome/browser/media/document_picture_in_picture_header/DocumentPictureInPictureHeaderMediator.java (L143-157)
```java
        mWebContentsObserver =
                new WebContentsObserver(mWebContents) {
                    @Override
                    public void didChangeVisibleSecurityState() {
                        updateSecurityIcon();
                    }
                };
        mOpenerWebContentsObserver =
                new WebContentsObserver(mOpenerWebContents) {
                    @Override
                    public void didChangeVisibleSecurityState() {
                        updateSecurityIcon();
                    }
                };
    }
```

**File:** chrome/android/java/src/org/chromium/chrome/browser/media/document_picture_in_picture_header/DocumentPictureInPictureHeaderMediator.java (L287-328)
```java
    private String getUrlString(@Nullable GURL url) {
        if (url == null || url.isEmpty() || !url.isValid()) {
            return "";
        }

        final String scheme = url.getScheme();
        if (UrlConstants.FILE_SCHEME.equals(scheme)) {
            // File scheme URLs do not have a host, so we use the path instead.
            return url.getPath();
        }

        if (UrlConstants.CONTENT_SCHEME.equals(scheme)) {
            return url.getSpec();
        }

        if (url.getHost().isEmpty()) {
            return "";
        }

        return UrlFormatter.formatUrlForSecurityDisplay(url, SchemeDisplay.OMIT_HTTP_AND_HTTPS);
    }

    private void updateSecurityIcon() {
        @ConnectionSecurityLevel
        int securityLevel = SecurityStateModel.getSecurityLevelForWebContents(mOpenerWebContents);
        @ConnectionMaliciousContentStatus
        int maliciousContentStatus =
                SecurityStateModel.getMaliciousContentStatusForWebContents(mWebContents);

        mModel.set(
                DocumentPictureInPictureHeaderProperties.SECURITY_ICON,
                SecurityStatusIcon.getSecurityIconResource(
                        securityLevel,
                        () -> maliciousContentStatus,
                        /* isSmallDevice= */ false,
                        /* skipIconForNeutralState= */ false,
                        /* useLockIconForSecureState= */ false,
                        /* isShowingHttpsFirstWarning= */ false));
        mModel.set(
                DocumentPictureInPictureHeaderProperties.SECURITY_ICON_CONTENT_DESCRIPTION_RES_ID,
                SecurityStatusIcon.getSecurityIconContentDescriptionResourceId(securityLevel));
    }
```

**File:** chrome/android/java/src/org/chromium/chrome/browser/media/document_picture_in_picture_header/DocumentPictureInPictureHeaderMediator.java (L330-337)
```java
    public void destroy() {
        mDesktopWindowStateManager.removeObserver(this);
        mThemeColorProvider.removeThemeColorObserver(this);
        mThemeColorProvider.removeTintObserver(this);
        mOpenerWebContentsObserver.observe(null);
        mWebContentsObserver.observe(null);
    }
}
```

**File:** chrome/android/java/src/org/chromium/chrome/browser/media/DocumentPictureInPictureActivity.java (L312-323)
```java
    @Override
    public void initializeCompositor() {
        // Guard against the asynchronous startup gap. Because initializeCompositor()
        // is posted to the UI thread, the opener WebContents could have navigated
        // to a different origin before the child WebContents delegate is attached.
        // If that happens, verify the origin to abort and prevent origin spoofing.
        if (mParentWebContents == null
                || mParentWebContents.isDestroyed()
                || !verifyOpenerOrigin(getIntent(), mParentWebContents)) {
            finish();
            return;
        }
```

**File:** chrome/android/java/src/org/chromium/chrome/browser/app/tab_activity_glue/PopupCreatorImpl.java (L580-592)
```java
        // Record the opener's origin at the time of the request to prevent origin spoofing
        // if the opener navigates before the Activity completes its launch.
        WebContents opener = webContents.getDocumentPictureInPictureOpener();
        if (opener != null) {
            intent.putExtra(
                    DocumentPictureInPictureActivity.INITIAL_OPENER_ORIGIN_KEY,
                    getOpenerOriginString(opener));
        }

        intent.setAction(Intent.ACTION_VIEW);

        return intent;
    }
```
