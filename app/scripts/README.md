# macOS release signing

The normal Tauri config uses ad-hoc signing for local development. Official builds use `tauri.release.conf.json` and the Developer ID Application identity installed in the login keychain. `build.rs` signs the embedded recorder with the same identity and hardened runtime.

From `app/`:

```sh
npm run release:mac
```

Once per Mac, store notarization credentials interactively (never commit a password or private key):

```sh
xcrun notarytool store-credentials tingo-notary --apple-id YOUR_APPLE_ID --team-id 9YJL5M4BY8
```

Enter an Apple Account app-specific password at the secure prompt. Then notarize the generated DMG:

```sh
bash scripts/notarize-macos.sh src-tauri/target/release/bundle/dmg/Tingo_0.5.0_aarch64.dmg
```

Use the actual version's filename. The script waits for acceptance, staples the ticket, checks Gatekeeper, and prints the final checksum. Generate release checksums **after** stapling. A signed build alone is not a notarized release. Keep signing assets and credentials outside the repository.
