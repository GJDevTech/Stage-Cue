# Stage Cue releases and automatic updates

Stage Cue 0.2.6 checks the repository's latest published GitHub release shortly
after startup. The repository address is stamped into packaged builds by the
included GitHub Actions workflow. Source runs intentionally do not have a
release repository configured.

The user can also select **Check for Updates** in the control-center toolbar.
Automatic checks can be disabled in **Display & Appearance**. Stage Cue asks
before downloading or installing and refuses to update while Present mode is
active.

## Publish a release

Commit and push the source, then create and push a version tag:

```bash
git tag v0.2.6
git push origin v0.2.6
```

The workflow publishes these GitHub Release assets:

- `Stage Cue.exe` (Windows x64)
- `Stage Cue.AppImage` (Linux x64)
- `Stage Cue-macOS-arm64.zip` containing `Stage Cue.app`
- `Stage Cue-macOS-x64.zip` containing `Stage Cue.app`

The architecture suffix is required only on the two macOS download archives
because one GitHub Release cannot contain two assets with the same filename.
The application itself is named `Stage Cue.app` on both architectures.

Windows users should keep `Stage Cue.exe` in a user-writable folder. Linux
users should make `Stage Cue.AppImage` executable. macOS users extract the ZIP
and keep `Stage Cue.app` in a location they can modify. Unsigned macOS builds
may still require the normal first-launch approval in Privacy & Security.
