/**
 * 走一遍登录页的三个动作：选身份 → 填账号密码 → 提交。
 *
 * RT-07/08、LG-*、TK-* 都要用。抄各自的 5 行会在「身份按钮文案改了」
 * 时留下三处不同的错，所以收在这里一份。
 */

import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

export type Identity = 'merchant' | 'customer'

export const IDENTITY_LABEL: Record<Identity, string> = {
  merchant: '商家登录',
  customer: '用户登录',
}

export interface Credentials {
  account?: string
  password?: string
}

export async function chooseIdentity(identity: Identity) {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: IDENTITY_LABEL[identity] }))
  return user
}

export async function fillAndSubmit({
  account = 'shop0001',
  password = 'pw123456',
}: Credentials = {}) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('账号'), account)
  await user.type(screen.getByLabelText('密码'), password)
  await user.click(screen.getByRole('button', { name: '登录' }))
}

export async function signIn(identity: Identity, credentials: Credentials = {}) {
  await chooseIdentity(identity)
  await fillAndSubmit(credentials)
}
