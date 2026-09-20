# Stage Cue releases and automatic updates

Stage Cue checks the repository's latest published GitHub release shortly
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
git tag v0.2.10
git push origin v0.2.10
```

The workflow publishes these GitHub Release assets:

- `StageCue.exe` (Windows x64)
- `StageCue.AppImage` (Linux x64)
- `StageCue-macOS-arm64.zip` containing `Stage Cue.app`
- `StageCue-macOS-x64.zip` containing `Stage Cue.app`

The architecture suffix is required only on the two macOS download archives
because one GitHub Release cannot contain two assets with the same filename.
The application itself is named `Stage Cue.app` on both architectures.

Windows users should keep `StageCue.exe` in a user-writable folder. Linux
users should make `StageCue.AppImage` executable. macOS users extract the ZIP
and keep `Stage Cue.app` in a location they can modify. Unsigned macOS builds
may still require the normal first-launch approval in Privacy & Security.
