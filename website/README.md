# Estera website

A standalone marketing website for Estera, the free GPL desktop app for simulating an iPhone’s location. The site is separate from the `web/` application and Python backend; it never requests geolocation, connects to a phone, or calls an app backend.

## Develop and build

```sh
cd website
npm ci
npm run dev
```

The development server uses `http://127.0.0.1:3001` with a strict port. `npm run build` compiles TypeScript and bundles the static site into `dist/` with Vite.

## Public release links

The header opens [Estera on GitHub](https://github.com/larsenbeenk2-ship-it/estera). Download buttons show system requirements, installation steps, and a direct link to `Estera-0.1.2-macos-arm64.zip` in release `v0.1.2`.

- The first binary is a macOS 15+ Apple Silicon prerelease, without Developer ID signing or Apple notarization. It includes the app runtime; users unzip it, move Estera into Applications, and launch it. Windows is a source-only preview.
- Pin and Journey are capabilities in the same free app. There is no advertised paid Pro tier.
- “Star on GitHub” opens the repository, where signed-in visitors can select Star. This is optional support, with no OAuth, star verification, download gate, or account requirement.
- Release URLs live in `src/release.ts`. When updating the version, also update the static no-JavaScript fallback in `index.html`.
- Publish the corresponding GitHub release and exact ZIP asset before deploying this site. The tag is explicit because GitHub’s latest-release endpoint does not select prereleases.

## Deploy to Vercel

Use `website` as the Vercel project’s root directory. `vercel.json` selects Vite, builds with `npm run build`, and publishes `dist`. The site uses section anchors rather than client-side routes, so no catch-all rewrite is required.

No environment variables, GitHub credentials, backend deployment, or paid services are needed for the website. Keep download binaries in GitHub Releases, outside the static site bundle.

## Night Atlas design

- A canvas globe projected from local Natural Earth geography; city selection, drag, keyboard, and scroll controls preserve the original design.
- An illustrative Pin/Journey product preview. Journey follows native scroll in both directions; play/pause/reset takes manual control, and reduced motion retains manual playback.
- A native download dialog, FAQ, accessible navigation, responsive layouts, static globe fallback, and print/forced-colors treatment.
- All geography is local. The site adds no analytics or external scripts.

`npm run build` is the release compilation check. No browser, screenshot, or visual QA is included in this change; rendered appearance and motion remain visually unverified.

## Design records

- [Experience and motion contract](EXPERIENCE.md)
- [Current direction](docs/DIRECTION.md)
- [Earlier research record](docs/RESEARCH.md)
- [Asset credits](public/assets/CREDITS.md)
- [Globe geography provenance](public/assets/atlas-PROVENANCE.md)

The design records preserve the earlier design process; this README and the current site describe release availability and the single free app.
