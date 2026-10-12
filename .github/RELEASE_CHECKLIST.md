# Manual release checks

CI tests and packages images. It does not publish releases or deploy the app.

- [ ] Confirm authorization for the exact release action, source SHA and version; retain existing tags/releases/assets unchanged.
- [ ] Set the intended immutable version in source before creating its new tag; a `v*` tag must match `app/version.py` exactly. Existing tags are never retargeted.
- [ ] Verify both workflows finished successfully for that tested source: source syntax/links/behavior, Windows Python 3.12, native AMD64 and native ARM64. Read test counts, skips and every required step; summary/report artifact presence is not overall success.
- [ ] Review actual browser interactions and every generated PDF/Word fixture page. Record unsupported or unrun platform checks.
- [ ] Download the image, inspection JSON and SHA256SUMS from that successful source run for each architecture. Verify checksums, OCI version/revision/architecture and actual packaged source identity.
- [ ] Check migration, backup, import/export and rollback instructions against the included implementation.
- [ ] Use explicit release authorization to create an immutable tag at the verified commit and publish only the six intended image/metadata/checksum assets. Do not upload archives, credentials, test databases, private screenshots or the optional Flent test image.
- [ ] Verify remote tag SHA, release flags, asset names/sizes/digests, download URLs and unchanged prior releases after publication. No automatic merge, force push, production deployment or stable/latest promotion.
