// Central registry of the ten feature modules: label, accent, icon, dashboard
// metric, and the copy shown when a module has nothing in it yet.
import type { ModuleKey } from './types'
import { money } from './format'
import {
  IcLoans, IcCards, IcShield, IcTrend, IcWallet, IcBell, IcCheck, IcImage, IcLock, IcDoc,
} from './icons'

interface ModuleDef {
  label: string
  color: string
  Icon: (p: { className?: string }) => React.ReactElement
  metric?: (n: number) => string
  /** Noun for the primary action, e.g. "Add expense". */
  addLabel: string
  /** One line explaining what this module is for — shown on the empty state.
   *  A blank screen that only says "nothing here" tells a new user nothing. */
  blurb: string
}

export const MODULES: Record<ModuleKey, ModuleDef> = {
  loans: {
    label: 'Loans', color: 'var(--c-loans)', Icon: IcLoans,
    metric: (n) => `${n} active`, addLabel: 'Add loan',
    blurb: 'Track EMIs, interest and outstanding balances, and tick each month off as you pay.',
  },
  cards: {
    label: 'Credit Cards', color: 'var(--c-cards)', Icon: IcCards,
    metric: (n) => `${n} cards`, addLabel: 'Add card',
    blurb: 'Keep every billing date in one place so a statement never slips past its due date.',
  },
  insurance: {
    label: 'Insurance', color: 'var(--c-insurance)', Icon: IcShield,
    metric: (n) => `${n} policies`, addLabel: 'Add policy',
    blurb: 'Store policy numbers, premiums and renewal dates, and get warned before they lapse.',
  },
  investments: {
    label: 'Investments', color: 'var(--c-investments)', Icon: IcTrend,
    metric: (n) => `${n} holdings`, addLabel: 'Add investment',
    blurb: 'Follow what you put in against what it is worth today, across every broker.',
  },
  expenses: {
    label: 'Expenses', color: 'var(--c-expenses)', Icon: IcWallet,
    metric: (n) => `${money(n, true)} this month`, addLabel: 'Add transaction',
    blurb: 'Log what you spend and earn — this is what drives your daily safe-to-spend figure.',
  },
  reminders: {
    label: 'Reminders', color: 'var(--c-reminders)', Icon: IcBell,
    metric: (n) => `${n} pending`, addLabel: 'Add reminder',
    blurb: 'Anything with a date — renewals, payments, follow-ups — surfaced before it is due.',
  },
  todo: {
    label: 'To-Do', color: 'var(--c-todo)', Icon: IcCheck,
    metric: (n) => `${n} pending`, addLabel: 'Add task',
    blurb: 'Simple tasks with priorities and due dates, alongside everything else you track.',
  },
  gallery: {
    label: 'Gallery', color: 'var(--c-gallery)', Icon: IcImage,
    metric: (n) => `${n} photos`, addLabel: 'Upload photos',
    blurb: 'Your private photo library, with people grouped automatically and duplicates found for you.',
  },
  vault: {
    label: 'Vault', color: 'var(--c-vault)', Icon: IcLock,
    metric: (n) => `${n} items`, addLabel: 'Add item',
    blurb: 'Passwords and logins, encrypted with AES-256 and revealed only when you ask.',
  },
  documents: {
    label: 'Documents', color: 'var(--c-documents)', Icon: IcDoc,
    metric: (n) => `${n} files`, addLabel: 'Add document',
    blurb: 'ID cards, policies and certificates — scanned, searchable and kept off the public web.',
  },
}
