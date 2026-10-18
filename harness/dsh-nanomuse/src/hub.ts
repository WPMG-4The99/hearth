/**
 * The hub as a client: this computer on the account's device list, the calls it
 * makes to the others and the few it answers (`docs/hub.md`, "Frames").
 *
 * One outbound WebSocket to `wss://…/v1/hub`, kept alive and reconnected with a
 * growing pause; the key travels in the `hello` frame, the way a browser sends
 * it, because Node's WebSocket cannot set headers. Plain `WebSocket`, no Cordis
 * — the service in `cloud.ts` owns the lifetime and the tests drive this
 * against a fake socket.
 */

/** A device as the relay lists it. */
export interface HubDevice {
  id: string
  name: string
  kind: string
  os: string
  version: string
  online: boolean
  last_seen: number
  controllable: boolean
  actions: string[]
}

/** The hub's `working` frame (contract C9): a device started or finished a turn on a synced conversation. */
export interface HubWorking {
  cid: string
  from: string
  device_name: string
  working: boolean
  /** Unix seconds, the relay's clock. */
  at: number
}

/** What this device says about itself in `hello`. */
export interface HelloDevice {
  id: string
  name: string
  kind: 'computer'
  os: string
  version: string
  actions: string[]
}

export interface Caller {
  id: string
  name: string
  kind: string
}

/** One call this device is answering: the id to reply with and a way to send progress. */
export interface IncomingCall {
  id: string
  from: Caller
  event(body: Record<string, unknown>): void
}

export type ActionHandler = (args: Record<string, unknown>, call: IncomingCall) => Promise<Record<string, unknown>>

export interface CallOptions {
  signal?: AbortSignal | undefined
  /** How long to wait for the `result`; the relay drops calls after 15 minutes anyway. */
  timeoutMs?: number | undefined
  /** Progress frames the target sends while it works (`event {id, body}`). */
  onEvent?: ((body: Record<string, unknown>) => void) | undefined
  /** The frame id to use, when the caller needs it beforehand (to `stop {call}` a task). */
  id?: string | undefined
}

/** The relay said no, or the target did. */
export class HubError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message)
    this.name = 'HubError'
  }
}

/** The subset of the WebSocket API the client uses; Node 22's global and a test double both fit. */
export interface SocketLike {
  readyState: number
  send(data: string): void
  close(code?: number, reason?: string): void
  addEventListener(type: 'open' | 'message' | 'close' | 'error', listener: (event: any) => void): void
}

export interface HubClientOptions {
  /** `wss://<relay>/v1/hub`. */
  url: string
  /** The account key when signed in; nothing when not — the client then waits. */
  key: () => Promise<string | undefined>
  device: () => HelloDevice
  /** Opens a socket; defaults to the global WebSocket. */
  socket?: (url: string) => SocketLike
  log?: (level: 'info' | 'warn' | 'debug', text: string) => void
  /** Reconnect pauses, for tests. */
  backoffMs?: { min: number; max: number }
  pingMs?: number
}

type Listener = () => void

interface Pending {
  resolve(body: Record<string, unknown>): void
  reject(error: Error): void
  onEvent?: ((body: Record<string, unknown>) => void) | undefined
  timer?: ReturnType<typeof setTimeout>
  cleanup(): void
}

const OPEN = 1
/** How long to wait after the relay closed with `hub_paused`: its operator switched the hub off for a while. */
const PAUSED_RETRY_MS = 120_000

export class HubClient {
  /** The relay's list, refreshed on every `devices` frame. */
  devices: HubDevice[] = []
  /** The id the relay confirmed in `welcome`; absent while disconnected. */
  deviceId: string | undefined
  connected = false
  /** The reason the last connection ended, for the UI. */
  lastError: string | undefined

  private socket: SocketLike | undefined
  private running = false
  private attempt = 0
  /** Counts `connect()` attempts; an attempt that finds a newer one when its key arrives gives up. */
  private generation = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined
  private pingTimer: ReturnType<typeof setInterval> | undefined
  /** Whether any frame arrived since the last ping went out (the pong, or anything else). */
  private heard = true
  private readonly pending = new Map<string, Pending>()
  private readonly handlers = new Map<string, ActionHandler>()
  private readonly listeners = {
    state: new Set<Listener>(),
    devices: new Set<Listener>(),
    profile: new Set<(rev: number, device: string) => void>(),
    sync: new Set<(cursor: number, device: string) => void>(),
    working: new Set<(frame: HubWorking) => void>(),
    unauthorized: new Set<Listener>(),
  }
  private seq = 0

  constructor(private readonly options: HubClientOptions) {}

  /** Keep a connection whenever a key is available; idempotent. */
  start(): void {
    if (this.running) return
    this.running = true
    this.attempt = 0
    void this.connect()
  }

  /** Close and stay closed; pending calls fail. */
  stop(reason = 'stopped'): void {
    this.running = false
    this.clearReconnect()
    this.closeSocket(1000, reason)
    this.failPending(new HubError('disconnected', reason))
    this.devices = []
    this.setConnected(false)
  }

  /** Reconnect now (a new key, a renamed device). */
  restart(): void {
    if (!this.running) return this.start()
    this.clearReconnect()
    this.closeSocket(1000, 'restart')
    // The old socket's close event is ignored once it is replaced, so its work ends here:
    // calls in flight fail now rather than at their timeout, and `connected` is false
    // until the new `welcome`, so a call made meanwhile is refused as offline instead of
    // being written into a socket that is not open yet.
    this.failPending(new HubError('disconnected', 'The hub connection is being restarted'))
    this.setConnected(false)
    this.attempt = 0
    void this.connect()
  }

  /** Answer `action` for the other devices; `info` has a default answer. */
  handle(action: string, handler: ActionHandler): () => void {
    this.handlers.set(action, handler)
    return () => {
      if (this.handlers.get(action) === handler) this.handlers.delete(action)
    }
  }

  onState(listener: Listener): () => void {
    this.listeners.state.add(listener)
    return () => this.listeners.state.delete(listener)
  }

  onDevices(listener: Listener): () => void {
    this.listeners.devices.add(listener)
    return () => this.listeners.devices.delete(listener)
  }

  /** The account's look changed on another device: fetch `rev`. */
  onProfile(listener: (rev: number, device: string) => void): () => void {
    this.listeners.profile.add(listener)
    return () => this.listeners.profile.delete(listener)
  }

  /** Another device pushed conversations (contract C7): pull from `cursor`. */
  onSync(listener: (cursor: number, device: string) => void): () => void {
    this.listeners.sync.add(listener)
    return () => this.listeners.sync.delete(listener)
  }

  /** Another device started or finished a turn on a synced conversation (contract C9). */
  onWorking(listener: (frame: HubWorking) => void): () => void {
    this.listeners.working.add(listener)
    return () => this.listeners.working.delete(listener)
  }

  /** The relay refused the key (close 4001): the owner should forget the account. */
  onUnauthorized(listener: Listener): () => void {
    this.listeners.unauthorized.add(listener)
    return () => this.listeners.unauthorized.delete(listener)
  }

  /** A device by id, or by name (case-insensitive), or the only other device. */
  find(ref: string): HubDevice | undefined {
    const wanted = ref.trim().toLowerCase()
    const others = this.devices.filter((d) => d.id !== this.deviceId && d.kind !== 'web')
    if (!wanted) return others.length === 1 ? others[0] : undefined
    return (
      others.find((d) => d.id.toLowerCase() === wanted) ??
      others.find((d) => d.name.toLowerCase() === wanted) ??
      others.find((d) => d.name.toLowerCase().includes(wanted)) ??
      (wanted === 'phone' || wanted === '手机' ? others.find((d) => d.kind === 'phone' && d.online) : undefined)
    )
  }

  /** Ask another device to do something; resolves with the `result` body. */
  call(to: string, action: string, args: Record<string, unknown>, options: CallOptions = {}): Promise<Record<string, unknown>> {
    if (!this.connected || !this.socket) return Promise.reject(new HubError('offline', 'This computer is not connected to the hub'))
    const id = options.id ?? this.nextId()
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const entry: Pending = {
        resolve,
        reject,
        onEvent: options.onEvent,
        cleanup: () => {
          if (entry.timer) clearTimeout(entry.timer)
          options.signal?.removeEventListener('abort', onAbort)
          this.pending.delete(id)
        },
      }
      const onAbort = () => {
        entry.cleanup()
        reject(new HubError('aborted', 'The call was stopped'))
      }
      if (options.signal?.aborted) return onAbort()
      options.signal?.addEventListener('abort', onAbort, { once: true })
      const timeoutMs = options.timeoutMs ?? 120_000
      entry.timer = setTimeout(() => {
        entry.cleanup()
        reject(new HubError('timeout', `${action}: the device did not answer within ${Math.round(timeoutMs / 1000)}s`))
      }, timeoutMs)
      this.pending.set(id, entry)
      this.send({ type: 'call', id, to, action, args })
    })
  }

  /** A fresh frame id, unique for this connection's lifetime. */
  nextId(): string {
    return `${Date.now().toString(36)}-${(++this.seq).toString(36)}`
  }

  /** Rename this device on the account's list. */
  rename(name: string): void {
    this.send({ type: 'rename', name: name.trim().slice(0, 60) })
  }

  /** Take an offline device off the list. */
  forget(deviceId: string): void {
    this.send({ type: 'forget', device_id: deviceId })
  }

  /** Ask for the list again. */
  refreshDevices(): void {
    this.send({ type: 'devices' })
  }

  // -- the socket -----------------------------------------------------------------------

  private async connect(): Promise<void> {
    if (!this.running || this.socket) return
    // `restart()` or `stop()` while the key is being read: this attempt lost its turn and
    // must not open a second socket next to the one the newer attempt opens.
    const generation = ++this.generation
    let key: string | undefined
    try {
      key = await this.options.key()
    } catch {
      key = undefined
    }
    if (!this.running || generation !== this.generation || this.socket) return
    if (!key) {
      // Not signed in: nothing to connect with. `restart()` is called at sign-in.
      return
    }
    const open = this.options.socket ?? ((url: string) => new WebSocket(url) as unknown as SocketLike)
    let socket: SocketLike
    try {
      socket = open(this.options.url)
    } catch (error: unknown) {
      this.lastError = message(error)
      this.scheduleReconnect()
      return
    }
    this.socket = socket
    const device = this.options.device()
    socket.addEventListener('open', () => {
      if (this.socket !== socket) return
      socket.send(JSON.stringify({ type: 'hello', key, device }))
    })
    socket.addEventListener('message', (event: { data: unknown }) => {
      if (this.socket !== socket) return
      this.receive(typeof event.data === 'string' ? event.data : String(event.data))
    })
    socket.addEventListener('error', () => {
      if (this.socket !== socket) return
      this.lastError = 'socket error'
    })
    socket.addEventListener('close', (event: { code?: number; reason?: string }) => {
      if (this.socket !== socket) return
      this.socket = undefined
      this.clearPing()
      const code = event.code ?? 1006
      const wasConnected = this.connected
      this.setConnected(false)
      this.failPending(new HubError('disconnected', 'The hub connection closed'))
      if (code === 4001) {
        this.lastError = 'the relay refused the key'
        this.running = false
        this.log('warn', 'hub: the relay refused the key; signed out')
        for (const listener of this.listeners.unauthorized) listener()
        return
      }
      if (code === 4002) {
        // The relay would not take this device's hello (docs/hub.md): another try with the
        // same device changes nothing, so stay closed; the UI shows why, the account stays.
        this.lastError = 'the relay refused this device'
        this.running = false
        this.log('warn', 'hub: the relay refused this device; not reconnecting')
        return
      }
      const paused = event.reason === 'hub_paused'
      if (paused) this.lastError = 'the hub is paused on the relay'
      else if (code === 4003) this.lastError = 'another connection of this device replaced this one'
      else if (code !== 1000) this.lastError = event.reason || `closed (${code})`
      if (wasConnected) this.log('info', `hub: disconnected (${code}${event.reason ? ` ${event.reason}` : ''})`)
      // The relay said why (docs/hub.md): paused by its operator, so wait a good while;
      // replaced by a newer connection of this device, so do not fight it; a hello it would
      // not take or too many frames (4008), which a retry a second later would not change.
      this.scheduleReconnect(paused ? PAUSED_RETRY_MS : code === 4003 ? 30_000 : code === 4000 || code === 4008 ? 60_000 : undefined)
    })
  }

  private receive(raw: string): void {
    let frame: Record<string, unknown>
    try {
      const parsed: unknown = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object') return
      frame = parsed as Record<string, unknown>
    } catch {
      return
    }
    this.heard = true
    switch (frame.type) {
      case 'welcome': {
        this.deviceId = String(frame.device_id ?? this.options.device().id)
        this.devices = toDevices(frame.devices)
        this.attempt = 0
        this.lastError = undefined
        this.setConnected(true)
        this.startPing()
        this.log('info', `hub: connected as ${this.deviceId}; ${this.devices.length} device(s) on the account`)
        for (const listener of this.listeners.devices) listener()
        return
      }
      case 'devices':
        this.devices = toDevices(frame.devices)
        for (const listener of this.listeners.devices) listener()
        return
      case 'profile':
        for (const listener of this.listeners.profile) listener(Number(frame.rev ?? 0), String(frame.device ?? ''))
        return
      case 'sync':
        if (frame.what === 'conversations') for (const listener of this.listeners.sync) listener(Number(frame.cursor ?? 0), String(frame.from ?? ''))
        return
      case 'working': {
        const working: HubWorking = { cid: String(frame.cid ?? ''), from: String(frame.from ?? ''), device_name: String(frame.device_name ?? ''), working: frame.working === true, at: Number(frame.at ?? 0) }
        for (const listener of this.listeners.working) listener(working)
        return
      }
      case 'pong':
        return
      case 'event': {
        const entry = this.pending.get(String(frame.id ?? ''))
        const body = frame.body
        if (entry?.onEvent && body && typeof body === 'object') entry.onEvent(body as Record<string, unknown>)
        return
      }
      case 'result': {
        const id = String(frame.id ?? '')
        const entry = this.pending.get(id)
        if (!entry) return
        entry.cleanup()
        if (frame.ok === false) {
          entry.reject(new HubError(String(frame.error ?? 'failed'), String(frame.message ?? frame.error ?? 'the device could not do that')))
        } else {
          const body = frame.body
          entry.resolve(body && typeof body === 'object' ? (body as Record<string, unknown>) : {})
        }
        return
      }
      case 'error': {
        const id = typeof frame.id === 'string' ? frame.id : ''
        const code = String(frame.code ?? 'error')
        const text = String(frame.message ?? code)
        const entry = id ? this.pending.get(id) : undefined
        if (entry) {
          entry.cleanup()
          entry.reject(new HubError(code, text))
        } else {
          this.lastError = text
          this.log('warn', `hub: ${code}: ${text}`)
          for (const listener of this.listeners.state) listener()
        }
        return
      }
      case 'call':
        void this.answer(frame)
        return
      default:
        return
    }
  }

  private async answer(frame: Record<string, unknown>): Promise<void> {
    const id = String(frame.id ?? '')
    const action = String(frame.action ?? '')
    const from = toCaller(frame.from)
    const args = frame.args && typeof frame.args === 'object' ? (frame.args as Record<string, unknown>) : {}
    const handler = this.handlers.get(action)
    if (!handler) {
      this.send({ type: 'result', id, ok: false, error: 'unknown_action', message: `this computer does not do '${action}'` })
      return
    }
    const call: IncomingCall = { id, from, event: (body) => this.send({ type: 'event', id, body }) }
    try {
      const body = await handler(args, call)
      this.send({ type: 'result', id, ok: true, body })
    } catch (error: unknown) {
      const code = error instanceof HubError ? error.code : 'failed'
      this.send({ type: 'result', id, ok: false, error: code, message: message(error) })
    }
  }

  private send(frame: Record<string, unknown>): void {
    const socket = this.socket
    if (!socket || socket.readyState !== OPEN) return
    try {
      socket.send(JSON.stringify(frame))
    } catch (error: unknown) {
      this.log('warn', `hub: send failed: ${message(error)}`)
    }
  }

  private setConnected(value: boolean): void {
    if (this.connected === value) return
    this.connected = value
    if (!value) this.deviceId = undefined
    for (const listener of this.listeners.state) listener()
  }

  private failPending(error: Error): void {
    for (const entry of [...this.pending.values()]) {
      entry.cleanup()
      entry.reject(error)
    }
  }

  private scheduleReconnect(atLeastMs?: number): void {
    if (!this.running || this.reconnectTimer) return
    const { min, max } = this.options.backoffMs ?? { min: 1000, max: 30_000 }
    const pause = Math.max(atLeastMs ?? 0, Math.min(max, min * 2 ** Math.min(this.attempt, 10)))
    this.attempt += 1
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined
      void this.connect()
    }, pause)
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.reconnectTimer = undefined
  }

  /**
   * A ping every `pingMs`, and the relay's silence as the sign of a dead link: a socket that
   * stays open after the network went away (a laptop that slept, a Wi-Fi that changed) sends
   * every frame into nothing and hears no `pong`. When nothing at all arrived between two
   * pings, the socket is dropped and the usual reconnect follows; the runtime's client does
   * the same with the protocol's own ping and a 20 s timeout.
   */
  private startPing(): void {
    this.clearPing()
    this.heard = true
    const every = this.options.pingMs ?? 25_000
    this.pingTimer = setInterval(() => {
      if (!this.heard) {
        this.dropSilent()
        return
      }
      this.heard = false
      this.send({ type: 'ping' })
    }, every)
  }

  /** The relay answered nothing for two ping intervals: the link is dead whatever the socket says. */
  private dropSilent(): void {
    this.log('warn', 'hub: the relay answered nothing for two pings; reconnecting')
    this.closeSocket(1000, 'no pong')
    this.lastError = 'the relay stopped answering'
    this.failPending(new HubError('disconnected', 'The hub connection stopped answering'))
    this.setConnected(false)
    this.scheduleReconnect()
  }

  private clearPing(): void {
    if (this.pingTimer) clearInterval(this.pingTimer)
    this.pingTimer = undefined
  }

  private closeSocket(code: number, reason: string): void {
    const socket = this.socket
    this.socket = undefined
    this.clearPing()
    if (!socket) return
    try {
      socket.close(code, reason)
    } catch {
      // already gone
    }
  }

  private log(level: 'info' | 'warn' | 'debug', text: string): void {
    this.options.log?.(level, text)
  }
}

function toDevices(value: unknown): HubDevice[] {
  if (!Array.isArray(value)) return []
  return value
    .filter((d): d is Record<string, unknown> => Boolean(d) && typeof d === 'object')
    .map((d) => ({
      id: String(d.id ?? ''),
      name: String(d.name ?? ''),
      kind: String(d.kind ?? ''),
      os: String(d.os ?? ''),
      version: String(d.version ?? ''),
      online: Boolean(d.online),
      last_seen: Number(d.last_seen ?? 0),
      controllable: Boolean(d.controllable),
      actions: Array.isArray(d.actions) ? d.actions.map(String) : [],
    }))
    .filter((d) => d.id)
}

function toCaller(value: unknown): Caller {
  const d = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  return { id: String(d.id ?? ''), name: String(d.name ?? ''), kind: String(d.kind ?? '') }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
