/**
 * The sign-in form in Settings → Account (phone number or e-mail, then the
 * emailed code), the same steps the first run walks through full-window. The
 * host does the talking to the relay; this only holds the form.
 */
import { Button, Input } from '@deepseek-ai/dsh-client-ui-primitives'
import { createElement as h, useState, type FormEvent, type ReactNode } from 'react'
import { useCloudConfig } from './AccountPage.tsx'
import { call, column, errorStyle, muted, row, type CloudStatus, type Translate, failureText } from './api.ts'

export interface SignInProps {
  t: Translate
  onSignedIn(status: CloudStatus): void
  /** Rendered under the form (the relay line, a "later" button…). */
  footer?: ReactNode
  /** The primary button takes the whole row (the welcome dialog). */
  wide?: boolean
}

export function SignIn({ t, onSignedIn, footer, wide = false }: SignInProps): ReactNode {
  const [step, setStep] = useState<'identifier' | 'code'>('identifier')
  const [identifier, setIdentifier] = useState('')
  const [code, setCode] = useState('')
  // A friend's invite code, as on the phone: optional, typed with the six-digit code; the relay
  // credits both when it is the first sign-in. Shown folded; a tap opens the field.
  const [invite, setInvite] = useState('')
  const [inviteOpen, setInviteOpen] = useState(false)
  const config = useCloudConfig()
  const [busy, setBusy] = useState(false)
  const [resent, setResent] = useState(false)
  const [error, setError] = useState<string | undefined>()

  const run = async (work: () => Promise<void>) => {
    setBusy(true)
    setError(undefined)
    try {
      await work()
    } catch (err: unknown) {
      setError(failureText(t, err))
    } finally {
      setBusy(false)
    }
  }

  const sendCode = (event?: FormEvent) => {
    event?.preventDefault()
    void run(async () => {
      await call('code', { identifier: identifier.trim() })
      setCode('')
      setStep('code')
    })
  }
  const resend = () => {
    void run(async () => {
      await call('code', { identifier: identifier.trim() })
      setResent(true)
      window.setTimeout(() => setResent(false), 4000)
    })
  }

  const signIn = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => {
      try {
        const status = await call<CloudStatus>('verify', { identifier: identifier.trim(), code: code.trim(), invite: invite.trim().toUpperCase() })
        setCode('')
        onSignedIn(status)
      } catch (err: unknown) {
        setCode('')
        throw err
      }
    })
  }

  const primary = { variant: 'primary' as const, size: 'md' as const, type: 'submit' as const, style: wide ? { width: '100%' } : undefined }
  const back = () => { setStep('identifier'); setError(undefined); setCode('') }

  if (step === 'code') {
    return h('form', { style: column, onSubmit: signIn },
      h('div', { style: muted },
        t('codeSentTo', { identifier: identifier.trim() }), ' ',
        h('button', { type: 'button', className: 'nm-ob-link nm-inline', disabled: busy, onClick: resend }, resent ? t('obResent') : t('obResend'))),
      h(Input, { value: code, onChange: (e: FormEvent<HTMLInputElement>) => setCode(e.currentTarget.value.replace(/\D/g, '').slice(0, 6)), placeholder: t('code'), inputMode: 'numeric', autoComplete: 'one-time-code', maxLength: 6, autoFocus: true, 'aria-label': t('code') }),
      inviteOpen
        ? h('div', { style: column },
            h(Input, { value: invite, onChange: (e: FormEvent<HTMLInputElement>) => setInvite(e.currentTarget.value.replace(/[^0-9a-zA-Z-]/g, '').slice(0, 16)), placeholder: t('obInviteCode'), autoComplete: 'off', autoCapitalize: 'characters', 'aria-label': t('obInviteCode') }),
            h('div', { style: muted }, t('obInviteHint', { bonus: config.invitee_bonus_cny ?? 5 })))
        : h('div', null, h('button', { type: 'button', className: 'nm-ob-link nm-inline', disabled: busy, onClick: () => setInviteOpen(true) }, t('obHaveInvite'))),
      error ? h('div', { style: errorStyle, role: 'alert' }, error) : null,
      h('div', { style: row },
        h(Button, { ...primary, disabled: busy || code.trim().length !== 6 }, busy ? t('signingIn') : t('signIn')),
        h(Button, { variant: 'ghost', size: 'md', type: 'button', disabled: busy, onClick: back }, t('back'))),
      footer)
  }

  return h('form', { style: column, onSubmit: sendCode },
    h(Input, { value: identifier, onChange: (e: FormEvent<HTMLInputElement>) => setIdentifier(e.currentTarget.value), placeholder: t('identifier'), autoComplete: 'username', 'aria-label': t('identifier') }),
    h('div', { style: muted }, t('identifierHint')),
    error ? h('div', { style: errorStyle, role: 'alert' }, error) : null,
    h('div', { style: row },
      h(Button, { ...primary, disabled: busy || identifier.trim().length < 3 }, busy ? t('sending') : t('sendCode'))),
    footer)
}
