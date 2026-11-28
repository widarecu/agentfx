import { describe, expect, it } from 'vitest'
import { resolveSourceSha, SOURCE_SHA_FILE, sourceShaFile } from '../../vite-plugins/sourceSha'

const SHA = 'a'.repeat(40)
const OTHER = 'b'.repeat(40)

function emitted(env: Record<string, string | undefined>) {
  const files: { fileName: string; source: string }[] = []
  const plugin = sourceShaFile(env)
  const hook = plugin.generateBundle as (...args: unknown[]) => void
  hook.call({ emitFile: (file: { fileName: string; source: string }) => files.push(file) })
  return files
}

describe('resolveSourceSha', () => {
  it('prefers VITE_SOURCE_SHA from the Deploy workflow', () => {
    expect(resolveSourceSha({ VITE_SOURCE_SHA: SHA, VERCEL_GIT_COMMIT_SHA: OTHER })).toBe(SHA)
  })

  it('normalises case and whitespace', () => {
    expect(resolveSourceSha({ VITE_SOURCE_SHA: ` ${SHA.toUpperCase()}\n` })).toBe(SHA)
  })

  it("falls back to Vercel's own commit SHA", () => {
    expect(resolveSourceSha({ VERCEL_GIT_COMMIT_SHA: OTHER })).toBe(OTHER)
  })

  it('is null for local builds', () => {
    expect(resolveSourceSha({})).toBeNull()
  })

  it('fails the build on a malformed VITE_SOURCE_SHA', () => {
    expect(() => resolveSourceSha({ VITE_SOURCE_SHA: 'abc123' })).toThrow(/40-character/)
  })
})

describe('sourceShaFile', () => {
  it('only runs for builds', () => {
    expect(sourceShaFile({}).apply).toBe('build')
  })

  it('writes the commit into source-sha.txt', () => {
    expect(emitted({ VITE_SOURCE_SHA: SHA })).toEqual([
      { type: 'asset', fileName: SOURCE_SHA_FILE, source: `${SHA}\n` },
    ])
    expect(SOURCE_SHA_FILE).toBe('source-sha.txt')
  })

  it('writes nothing when the commit is unknown', () => {
    expect(emitted({})).toEqual([])
  })
})
