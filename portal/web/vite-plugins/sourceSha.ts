import type { Plugin } from 'vite'

// The Deploy workflow (scripts/deploy_staging.py guard / verify) reads this
// file from the live site to learn which commit the frontend was built from
// (AFX-129). Vercel serves it from the site root.
export const SOURCE_SHA_FILE = 'source-sha.txt'

const SHA = /^[0-9a-f]{40}$/

type Env = Record<string, string | undefined>

/**
 * The commit this build comes from: `VITE_SOURCE_SHA` (set by the Deploy
 * workflow), else Vercel's own `VERCEL_GIT_COMMIT_SHA`. Null when neither is
 * set, e.g. local and E2E builds. A malformed `VITE_SOURCE_SHA` throws, so a
 * broken deploy fails at build time instead of reporting nothing.
 */
export function resolveSourceSha(env: Env): string | null {
  const explicit = (env.VITE_SOURCE_SHA ?? '').trim().toLowerCase()
  if (explicit) {
    if (!SHA.test(explicit)) {
      throw new Error(`VITE_SOURCE_SHA is not a 40-character commit SHA: "${explicit}"`)
    }
    return explicit
  }
  const vercel = (env.VERCEL_GIT_COMMIT_SHA ?? '').trim().toLowerCase()
  return SHA.test(vercel) ? vercel : null
}

/** Emits `source-sha.txt` into the build output when the commit is known. */
export function sourceShaFile(env: Env = process.env): Plugin {
  return {
    name: 'agentfx-source-sha',
    apply: 'build',
    generateBundle() {
      const sha = resolveSourceSha(env)
      if (sha) {
        this.emitFile({ type: 'asset', fileName: SOURCE_SHA_FILE, source: `${sha}\n` })
      }
    },
  }
}
