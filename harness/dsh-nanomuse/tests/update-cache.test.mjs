// The update check's cache against the bundle that runs now: the state file outlives an
// update, so the check the previous version made within the day said "1.0.2 is out" to the
// 1.0.2 that had just been installed (#341). The cache is read against this version, and
// `update(false)` does not answer from a cache another version made.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Service } from '@deepseek-ai/cordis'
import NanomuseCloud, { BUNDLE_VERSION, LLM_ROW, updateForThisVersion } from '../lib/cloud.js'
import { UPDATE_EVERY_MS, UPDATE_RETRY_MS, updateCacheFresh } from '../lib/desk.js'

process.env.NANOMUSE_PY = join(tmpdir(), 'nanomuse-runtime-that-does-not-exist')

function fakeContext() {
  const settings = { [LLM_ROW]: { providers: {} } }
  const credentials = new Map()
  return {
    reflect: { provide() {} },
    logger: { info() {}, warn() {}, debug() {}, error() {} },
    credentials: {
      async set(ref, value) { credentials.set(String(ref), value) },
      async resolve(ref) { return credentials.has(String(ref)) ? { value: credentials.get(String(ref)) } : undefined },
      async unset(ref) { credentials.delete(String(ref)) },
    },
    settings: {
      async update(ns, patch) { Object.assign(settings[ns] ?? (settings[ns] = {}), patch) },
      async mutate() {},
      describe() { return Object.entries(settings).map(([ns, value]) => ({ ns, value })) },
    },
    get() { return undefined },
    inject() {},
    effect(fn) { fn() },
    on() { return () => undefined },
  }
}

/** A service over a state file that already holds `update`, as the previous version left it. */
async function cloudWith(update) {
  const home = await mkdtemp(join(tmpdir(), 'nm-update-cache-'))
  const statePath = join(home, 'cloud.json')
  await writeFile(statePath, JSON.stringify({ deviceId: 'pc-dsh-test', update }))
  const svc = new NanomuseCloud(fakeContext(), { baseURL: 'https://relay.invalid', deviceName: 'test-desktop', statePath })
  svc.hub.start = () => undefined
  svc.refresh = async () => svc.status()
  await svc[Service.init]()
  return { svc, done: () => rm(home, { recursive: true, force: true }) }
}

/** The version `package.json` carries: the bundle under test must report it, whatever the release. */
const PKG_VERSION = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version
/** A version below any release the tests run under. */
const PREVIOUS = '0.1.0'
/** One patch release beyond the version under test. */
const BEYOND = PKG_VERSION.replace(/(\d+)$/, (n) => String(Number(n) + 1))
const checkedByPrevious = (latest) => ({ current: PREVIOUS, latest, newer: true, page: 'https://github.com/WPMG-4The99/hearth/releases', assets: [], notes: '', source: 'github', checkedAt: Date.now() })

test('the bundle version the tests run under is the package version, a release, not the fallback', () => {
  assert.equal(BUNDLE_VERSION, PKG_VERSION)
  assert.match(BUNDLE_VERSION, /^\d+\.\d+\.\d+$/)
  assert.notEqual(BUNDLE_VERSION, PREVIOUS)
})

test('a cached check made by the previous version is read against this one', () => {
  // installed the release the cache announced: nothing is newer any more
  const same = updateForThisVersion(checkedByPrevious(BUNDLE_VERSION))
  assert.equal(same.current, BUNDLE_VERSION)
  assert.equal(same.latest, BUNDLE_VERSION)
  assert.equal(same.newer, false)
  // a release beyond the one installed is still newer
  const beyond = updateForThisVersion(checkedByPrevious(BEYOND))
  assert.equal(beyond.current, BUNDLE_VERSION)
  assert.equal(beyond.newer, true)
  // the time of the check is kept: no extra request on this start
  const cached = checkedByPrevious(BUNDLE_VERSION)
  assert.equal(updateForThisVersion(cached).checkedAt, cached.checkedAt)
  // a cache this version made is returned as it is
  const own = { ...checkedByPrevious(BEYOND), current: BUNDLE_VERSION }
  assert.equal(updateForThisVersion(own), own)
  // a cache without a `latest` (an older state file) says nothing is newer rather than throwing
  const bare = updateForThisVersion({ checkedAt: 1 })
  assert.equal(bare.newer, false)
  assert.equal(bare.latest, '')
})

test('right after installing this version over the previous one, the sidebar is not told to update to it', async () => {
  const out = await cloudWith(checkedByPrevious(BUNDLE_VERSION))
  try {
    const live = out.svc.live()
    assert.equal(live.update.current, BUNDLE_VERSION)
    assert.equal(live.update.latest, BUNDLE_VERSION)
    assert.equal(live.update.newer, false)
  } finally {
    await out.done()
  }
})

test('a failed check is cached an hour, a successful one a day, and another version\'s never', () => {
  const now = Date.now()
  const good = { ...checkedByPrevious(BEYOND), current: BUNDLE_VERSION, checkedAt: now }
  const failed = { ...good, latest: '', newer: false, source: 'none', error: 'github: 403' }
  assert.equal(updateCacheFresh(good, BUNDLE_VERSION, now + UPDATE_EVERY_MS - 1), true)
  assert.equal(updateCacheFresh(good, BUNDLE_VERSION, now + UPDATE_EVERY_MS), false)
  assert.equal(updateCacheFresh(failed, BUNDLE_VERSION, now + UPDATE_RETRY_MS - 1), true)
  assert.equal(updateCacheFresh(failed, BUNDLE_VERSION, now + UPDATE_RETRY_MS), false, 'a failure is tried again after an hour')
  assert.ok(UPDATE_RETRY_MS < UPDATE_EVERY_MS)
  assert.equal(updateCacheFresh(undefined, BUNDLE_VERSION, now), false)
  assert.equal(updateCacheFresh({ ...good, current: PREVIOUS }, BUNDLE_VERSION, now), false)
})

test('a check that read nothing is not kept for the day: the next quiet check asks again, the About row asks at once', async () => {
  const out = await cloudWith(undefined)
  const realFetch = globalThis.fetch
  let asked = 0
  let down = true
  // GitHub is down first, then it answers; nothing leaves the test
  globalThis.fetch = async () => {
    asked += 1
    if (down) return { ok: false, status: 503, json: async () => ({}) }
    return { ok: true, status: 200, json: async () => ({ tag_name: `v${BUNDLE_VERSION}`, html_url: 'https://github.com/WPMG-4The99/hearth/releases/latest', assets: [] }) }
  }
  try {
    const failed = await out.svc.update(false)
    assert.equal(failed.source, 'none')
    assert.ok(failed.error)
    assert.equal(asked, 1, 'GitHub, once')
    // within the hour the failure stands for a quiet check...
    asked = 0
    assert.equal(await out.svc.update(false), failed)
    assert.equal(asked, 0)
    // ...never for the About row's button
    down = false
    const forced = await out.svc.update(true)
    assert.equal(asked, 1)
    assert.equal(forced.source, 'github')
    assert.equal(forced.latest, BUNDLE_VERSION)
    // a failure older than an hour is asked again by a quiet check, where a success would still be cached
    down = true
    asked = 0
    out.svc.state.update = { ...failed, checkedAt: Date.now() - UPDATE_RETRY_MS - 1 }
    assert.equal((await out.svc.update(false)).source, 'none')
    assert.equal(asked, 1)
    asked = 0
    out.svc.state.update = { ...forced, checkedAt: Date.now() - UPDATE_RETRY_MS - 1 }
    assert.equal((await out.svc.update(false)).source, 'github')
    assert.equal(asked, 0)
  } finally {
    globalThis.fetch = realFetch
    await out.done()
  }
})

test('update(false) does not answer from a cache another version made; it checks again', async () => {
  const out = await cloudWith(checkedByPrevious(BUNDLE_VERSION))
  const realFetch = globalThis.fetch
  const asked = []
  // GitHub answers that this very version is the latest; nothing leaves the test
  globalThis.fetch = async (url) => { asked.push(String(url)); return { ok: true, status: 200, json: async () => ({ tag_name: `v${BUNDLE_VERSION}`, html_url: 'https://github.com/WPMG-4The99/hearth/releases/latest', assets: [] }) } }
  try {
    // the stale cache, put back in memory as if nothing had normalised it
    const stale = checkedByPrevious(BUNDLE_VERSION)
    out.svc.state.update = stale
    const info = await out.svc.update(false)
    assert.notEqual(info, stale, 'not the stale object')
    assert.equal(asked.length, 1, 'one request, to GitHub')
    assert.equal(info.current, BUNDLE_VERSION)
    assert.equal(info.newer, false)
    // a cache this version made within the day is answered without a request
    asked.length = 0
    assert.equal(await out.svc.update(false), info)
    assert.equal(asked.length, 0)
  } finally {
    globalThis.fetch = realFetch
    await out.done()
  }
})
