// This computer's hands for the other devices: the shapes and limits the runtime's
// `nanomuse/hub/actions.py` has, so a phone cannot tell the two apart.
import assert from 'node:assert/strict'
import { mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { brief, expand, fileGet, filePut, files, open, pngSize, run, screen, shell, windowsOpener } from '../lib/actions.js'

const posix = process.platform !== 'win32'

async function scratch(work) {
  // realpath: macOS hands out /var/folders/… for a directory that is really under /private/var, and `pwd` says so
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'nanomuse-actions-')))
  try {
    return await work(dir)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

test('expand: ~, env vars and relative paths land under home', () => {
  const home = posix ? '/home/someone' : 'C:\\Users\\someone'
  assert.equal(expand('~', home), home)
  assert.equal(expand(undefined, home), home)
  assert.ok(expand('~/Desktop', home).endsWith('Desktop'))
  assert.ok(expand('notes', home).startsWith(home))
  process.env.NANOMUSE_TEST_DIR = home
  assert.equal(expand('$NANOMUSE_TEST_DIR', home), home)
  assert.equal(expand('%NANOMUSE_TEST_DIR%', home), home)
  delete process.env.NANOMUSE_TEST_DIR
})

test('shell: exit code, output, cwd and a timeout that kills the command', { skip: !posix }, async () => {
  const ok = await shell({ command: 'echo hello; echo oops >&2; exit 3' })
  assert.equal(ok.exit_code, 3)
  assert.equal(ok.stdout, 'hello\n')
  assert.equal(ok.stderr, 'oops\n')
  assert.equal(ok.timed_out, false)
  assert.ok(typeof ok.duration_ms === 'number')

  await scratch(async (dir) => {
    const here = await shell({ command: 'pwd', cwd: dir })
    assert.equal(here.stdout.trim(), dir)
  })

  const slow = await shell({ command: 'sleep 5; echo late', timeout: 1 })
  assert.equal(slow.timed_out, true)
  assert.equal(slow.exit_code, 124)
  assert.ok(slow.duration_ms < 4000, `took ${slow.duration_ms}ms`)

  await assert.rejects(shell({ command: '   ' }), (e) => e.code === 'usage')
})

test('files, file.get and file.put: listing, limits and the force rule', async () => {
  await scratch(async (dir) => {
    await writeFile(join(dir, 'b.txt'), 'bee')
    await writeFile(join(dir, 'a.json'), '{}')
    await filePut({ path: join(dir, 'sub', 'c.bin'), data: Buffer.from('cee').toString('base64') })

    const listing = await files({ path: dir })
    assert.equal(listing.path, dir)
    assert.deepEqual(listing.entries.map((e) => [e.name, e.type]), [['sub', 'dir'], ['a.json', 'file'], ['b.txt', 'file']])
    assert.equal(listing.entries[2].size, 3)

    const one = await files({ path: join(dir, 'b.txt') })
    assert.equal(one.entries.length, 1)
    assert.equal(one.entries[0].name, 'b.txt')

    await assert.rejects(files({ path: join(dir, 'missing') }), (e) => e.code === 'not_found')

    const got = await fileGet({ path: join(dir, 'a.json') })
    assert.equal(got.name, 'a.json')
    assert.equal(got.mime, 'application/json')
    assert.equal(got.bytes, 2)
    assert.equal(Buffer.from(got.data, 'base64').toString(), '{}')
    await assert.rejects(fileGet({ path: dir }), (e) => e.code === 'not_found')

    await assert.rejects(filePut({ path: join(dir, 'b.txt'), data: '' }), (e) => e.code === 'exists')
    await assert.rejects(filePut({ path: dir, data: '' }), (e) => e.code === 'is_dir')
    const replaced = await filePut({ path: join(dir, 'b.txt'), data: Buffer.from('new').toString('base64'), force: true })
    assert.equal(replaced.bytes, 3)
    assert.equal(await readFile(join(dir, 'b.txt'), 'utf8'), 'new')
    await assert.rejects(filePut({ data: '' }), (e) => e.code === 'usage')
  })
})

test('open: a URL goes to the platform opener, a missing path is refused', async () => {
  const calls = []
  const fake = { platform: 'linux', env: {}, run: async (file, args) => { calls.push([file, ...args]); return 0 } }
  const opened = await open({ url: 'https://example.com/' }, fake)
  assert.deepEqual(opened, { ok: true, url: 'https://example.com/' })
  assert.deepEqual(calls, [['xdg-open', 'https://example.com/']])

  const mac = { platform: 'darwin', env: {}, run: async (file) => (file === 'open' ? 0 : 1) }
  assert.equal((await open({ url: 'https://example.com/' }, mac)).ok, true)

  const broken = { platform: 'linux', env: {}, run: async () => { throw new Error('no xdg-open') } }
  assert.equal((await open({ url: 'https://example.com/' }, broken)).ok, false)

  await assert.rejects(open({ url: '' }, fake), (e) => e.code === 'usage')
  await assert.rejects(open({ url: '/definitely/not/here/nanomuse' }, fake), (e) => e.code === 'not_found')

  // Windows: PowerShell's Start-Process with the target as an encoded literal, so cmd never reads %NAME% or & in a URL
  const winCalls = []
  const win = { platform: 'win32', env: {}, run: async (file, args) => { winCalls.push([file, ...args]); return 0 } }
  const url = "https://example.invalid/?q=%USERNAME%&x='1'"
  assert.equal((await open({ url }, win)).ok, true)
  const [file, ...args] = winCalls[0]
  assert.equal(file, 'powershell.exe')
  assert.ok(args.includes('-NonInteractive') && args.includes('-EncodedCommand'))
  const script = Buffer.from(args[args.length - 1], 'base64').toString('utf16le')
  assert.equal(script, "Start-Process -FilePath 'https://example.invalid/?q=%USERNAME%&x=''1'''")
  const local = windowsOpener("C:\\Users\\me\\it's.pdf")
  assert.equal(Buffer.from(local.args[local.args.length - 1], 'base64').toString('utf16le'), "Start-Process -FilePath 'C:\\Users\\me\\it''s.pdf'")
})

test('screen: no display means no_screen; a tool that writes a PNG is read back', async () => {
  await assert.rejects(screen({ platform: 'linux', env: {}, run: async () => 0 }), (e) => e.code === 'no_screen')

  const png = Buffer.alloc(33)
  png.write('\x89PNG\r\n\x1a\n', 0, 'latin1')
  png.writeUInt32BE(13, 8)
  png.write('IHDR', 12, 'latin1')
  png.writeUInt32BE(640, 16)
  png.writeUInt32BE(480, 20)
  assert.deepEqual(pngSize(png), { width: 640, height: 480 })
  assert.deepEqual(pngSize(Buffer.from('nope')), { width: 0, height: 0 })

  const tried = []
  const fake = {
    platform: 'linux',
    env: { DISPLAY: ':0' },
    run: async (file, args) => {
      tried.push(file)
      if (file !== 'grim') return 1
      await writeFile(args[args.length - 1], png)
      return 0
    },
  }
  const shot = await screen(fake)
  assert.equal(shot.mime, 'image/png')
  assert.equal(shot.width, 640)
  assert.equal(shot.height, 480)
  assert.equal(shot.bytes, 33)
  assert.equal(Buffer.from(shot.data, 'base64').length, 33)
  assert.deepEqual(tried, ['gnome-screenshot', 'spectacle', 'grim'])
})

test('run dispatches by hub name; brief says what was asked', async () => {
  await scratch(async (dir) => {
    const listing = await run('files', { path: dir })
    assert.equal(listing.path, dir)
  })
  assert.equal(brief('shell', { command: '  ls   -la\n' }), 'ls -la')
  assert.equal(brief('files', {}), '~')
  assert.equal(brief('open', { url: 'https://example.com/' }), 'https://example.com/')
  assert.equal(brief('screen', {}), '')
  assert.equal(brief('shell', { command: 'x'.repeat(100) }).length, 80)
})
