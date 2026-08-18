// Shared domain types mirroring the FastAPI response shapes.

export type ModuleKey =
  | 'loans' | 'cards' | 'insurance' | 'investments' | 'expenses'
  | 'reminders' | 'todo' | 'habits' | 'gallery' | 'vault' | 'documents'

export interface DocumentItem {
  id: number
  title: string
  category: string
  doc_number: string | null
  issue_date: string | null
  issue_fmt: string | null
  expiry_date: string | null
  expiry_fmt: string | null
  days_until_expiry: number | null
  expiry_status: 'expired' | 'soon' | 'ok' | null
  notes: string | null
  ext: string
  mime: string
  is_pdf: boolean
  /** Renderable in an <img>; everything else is offered as a download. */
  is_image: boolean
  size_bytes: number
  pages: number
  file_url: string
  thumb_url: string | null
  is_favourite: number
  is_trashed: number
  trashed_fmt: string | null
  created_at: string | null
}

export interface DocumentsData {
  items: DocumentItem[]
  total: number
  counts: Record<string, number>
  trashed: number
}

export interface MasterItem {
  id: number
  type: string
  key: string
  label: string
  emoji: string | null
  color: string | null
  sort_order: number
  is_active: number
}

export interface MasterTypeMeta {
  /// Row id. These are rows now, not entries in a dict on the server — a
  /// person can add lists of their own, so the four built-ins are simply the
  /// ones marked is_builtin.
  id: number
  /// The identity, and never editable: values point at their list through it
  /// and the record forms name `expense_category` and `bank` in code.
  type: string
  label: string
  field: 'emoji' | 'color'
  icon?: string | null
  is_builtin?: number
  count?: number
}

export interface User {
  id: number
  name: string
  email: string
  role: 'admin' | 'user'
  /**
   * Whether this account may actually reach the admin API — decided by the
   * server, not worked out here from `role`.
   *
   * In a licensed copy there is no administrator by design, so a row that says
   * admin is a mistake rather than a permission. Drawing the admin screens from
   * `role` offered a customer User management, Licences and the whole-installation
   * export while every one of those calls came back 403.
   */
  can_admin?: boolean
  status: 'active' | 'suspended'
  phone?: string | null
  initials: string
  /** Signed, expiring URL for the profile photo; null when none is set. */
  avatar_url?: string | null
}

export interface Session {
  token: string
  user: User
  modules: ModuleKey[]
}

export interface DashboardData {
  stats: {
    investValue: number
    investDelta: number
    monthSpend: number
    monthIncome: number
    outstanding: number
    duesCount: number
  }
  moduleTotals: Record<string, number>
  moduleAttention?: Record<string, number>
  upcoming: { title: string; module: string; due: string | null; days: number | null }[]
}

export interface Loan {
  id: number; lender: string; loan_type: string; principal: number
  interest_rate: number; emi: number; tenure_months: number; outstanding: number
  start_date: string | null; next_due_date: string | null; status: string; notes?: string | null
  emi_day?: number; period?: string; paid_this_month?: boolean; paid_date?: string | null
  last_paid?: string | null; next_emi?: string; next_emi_fmt?: string; days_until?: number
}

export interface Card {
  id: number; bank: string; last4: string; credit_limit: number
  billing_day: number; due_date: string | null
  due_day: number; next_due: string; next_due_fmt: string; days_until: number
  period?: string; paid_this_month?: boolean; paid_date?: string | null; last_paid?: string | null
}

export interface CardPayment {
  period: string; period_label: string; paid_date: string | null; amount: number | null
}

export interface Insurance {
  id: number; policy_type: string; provider: string; policy_no: string
  premium: number; sum_assured: number; frequency: string; renewal_date: string | null
}

export interface Investment {
  id: number; broker: string; invest_type: string; name: string
  invested_amount: number; current_value: number; units: number; maturity_date: string | null
}

export interface Expense {
  id: number; kind: 'income' | 'expense'; category: string; amount: number
  method: string | null; txn_date: string | null; note: string | null
}

export interface Reminder {
  id: number; title: string; module_ref: string | null; due_date: string | null
  // "18:30", or null for one that is simply due that day. time_fmt is the same
  // value read aloud ("6:30 pm") and is only present when due_time is.
  due_time: string | null; time_fmt?: string | null
  recurrence: string | null; is_done: number; days: number | null; due_fmt: string | null
}

export interface Todo {
  id: number; title: string; priority: 'low' | 'medium' | 'high'
  due_date: string | null; status: 'pending' | 'done'
  recurrence: string | null
}

export interface HabitDay {
  date: string; active: boolean; done: boolean; count: number
}

export interface Habit {
  id: number
  name: string
  icon: string
  color: string
  kind: 'build' | 'quit'
  goal_type: 'daily' | 'weekdays' | 'weekly'
  weekdays: string          // CSV of ISO weekday numbers, Mon=1…Sun=7
  target_count: number
  unit: string
  weekly_target: number
  reminder_time: string | null
  note: string | null
  archived: number
  sort_order: number
  // Computed by the server on every read:
  target: number
  today_count: number
  done_today: boolean
  active_today: boolean
  current_streak: number
  best_streak: number
  rate30: number
  week: HabitDay[]
}

export interface VaultItem {
  id: number; title: string; username: string | null; url: string | null
  category: string | null; has_password: boolean
}

export interface AdminUser extends User {
  modules_granted: number
  last_login: string | null
}

export interface Photo {
  id: number
  url: string
  thumb_url: string
  is_favourite: number
  taken_at: string | null
  taken_fmt?: string | null
  caption: string | null
}

/** Photo + everything read from its EXIF. Any field may be null: shared photos,
 *  screenshots and older uploads simply never carried that information. */
export interface PhotoDetail extends Photo {
  orig_name: string | null
  width: number | null
  height: number | null
  megapixels: number | null
  size_bytes: number
  camera: string | null
  lens: string | null
  lat: number | null
  lon: number | null
  shot_at: string | null
  uploaded_at: string | null
}

export interface PhotoInfo {
  photo: PhotoDetail
  people: { id: number; name: string }[]
  albums: { id: number; name: string }[]
}

export interface InboxItem {
  id: number
  kind: string
  title: string
  body: string
  url: string
  read: boolean
  /** False when the push was never accepted for delivery — shown as "in-app only". */
  pushed: boolean
  at: string | null
}

export interface ActivityChange { field: string; from: string | null; to: string | null }

export interface ActivityRow {
  id: number
  at: string | null
  action: string
  verb: string
  title: string
  tone: 'ok' | 'info' | 'warn' | 'danger'
  entity: string | null
  entity_label: string
  entity_id: number | null
  label: string | null
  by: string
  by_id: number | null
  mine: boolean
  ip: string | null
  security: boolean
  changes: ActivityChange[]
  extra: Record<string, unknown>
}

export interface ActivitySummary {
  days: number
  total: number
  buckets: { added: number; edited: number; deleted: number; security: number }
  top: { label: string; count: number }[]
  /** Parts of the app this user has actually used, biggest first. */
  modules: { key: string; label: string; count: number }[]
  tracking_since: string
}

/** Progress of the background pass that reads photos for faces and content. */
export interface IndexStatus {
  running: boolean
  job: string
  done: number
  total: number
  error: string | null
  faces_found: number
  people: number
  /** Photos that contained a face but were judged a document/screenshot. */
  skipped_documents: number
  models: { faces: boolean; clip: boolean }
  pending: { faces: number; clip: number }
  photos: number
}

export interface AlbumSummary {
  id: number
  name: string
  count: number
  cover_url: string | null
  created_at: string | null
}

export interface PersonSummary {
  id: number
  name: string
  count: number
  cover_url: string | null
}

export interface MemoryGroup {
  years: number
  label: string
  items: Photo[]
}

export interface DuplicateGroup {
  hash: string
  count: number
  keep_id: number
  items: Photo[]
}

export interface DuplicatesData {
  groups: DuplicateGroup[]
  group_count: number
  extra: number
  distance?: number  // similar-mode only: perceptual Hamming threshold used
  skipped?: number   // similar-mode only: over-large clusters left out for safety
}

export interface BriefingBill {
  type: 'card' | 'loan'; id: number; name: string; sub: string
  amount: number | null; due_fmt: string; days: number
}

export interface BriefingDue {
  module: string; kind: 'bill' | 'renewal' | 'reminder' | 'task'; payType?: 'card' | 'loan'
  id: number; title: string; sub: string; amount: number | null
  due_fmt: string; days: number; payable: boolean
}

export interface BriefingData {
  date: string
  safeToSpend: {
    hasIncome: boolean; remainingToday: number; allowanceToday: number; spentToday: number
    overBudget: boolean; ringPct: number; savingsTarget: number; monthlyBudget: number
    spentMonth: number; monthOver: number
  }
  spentToday: number; incomeMonth: number
  bills: BriefingBill[]; billsTotal: number; billsCount: number
  dues: BriefingDue[]; duesCount: number
  streak: { current: number; todayLogged: boolean }
  memory: { thumb_url: string; years: number; label: string } | null
}

/** A computer the app has run on. The app is portable, so "which machine is
 *  serving this?" is a real question — especially when two are running at once. */
export interface AppHost {
  id: number | null
  hostname: string
  platform: 'windows' | 'mac' | 'linux'
  os_name: string
  local_ip: string
  public_url: string
  app_version: string
  data_dir: string
  first_seen: string | null
  last_seen: string | null
  is_current: boolean
}

export interface HostReport {
  current: AppHost
  history: AppHost[]
  /** How many times the app has changed computers. */
  moves: number
}

export interface StorageSlice { files: number; bytes: number }

export interface StorageUsage {
  modules: { gallery: StorageSlice; documents: StorageSlice; avatars: StorageSlice }
  files: number
  bytes: number
}

export interface StorageReport {
  /** Always present: what the signed-in user is storing. */
  mine: StorageUsage
  /** Admin only — everyone's files, the database, and room left on the drive. */
  server: StorageUsage | null
  database: number | null
  disk: { free: number; total: number } | null
}

export type LicenceState = 'ok' | 'expiring' | 'grace' | 'expired' | 'revoked' | 'invalid' | 'missing' | 'suspended'

/** A licence this installation has issued to somebody else. */
export interface Licence {
  id: number
  key_id: string
  name: string
  email: string
  role: string
  note: string | null
  issued_on: string | null
  expires_on: string | null
  days_left: number | null
  /** Sold outright: no end date. expires_on and days_left are null when true. */
  perpetual?: boolean
  /** Sign-ins the household may have; 0 is unlimited. */
  seats?: number
  state: LicenceState
  revoked_at: string | null
  revoke_reason: string | null
  bundle_at: string | null
  created_at: string | null
  /** Their own web address, when you host them. */
  hostname: string | null
  url: string | null
  hosted: boolean
  suspended: boolean
  suspended_at: string | null
  suspend_reason: string | null
  /** What the customer's copy reported when it last checked in. Operational
   *  facts about the machine and the build — never anything about their data. */
  last_seen: string | null
  last_ip: string | null
  last_platform: string | null
  last_os: string | null
  last_version: string | null
  last_hostname: string | null
  checkins: number
  /** Activation lock (Option B): whether this key has been claimed by a machine. */
  activated: boolean
  activated_at: string | null
  /** Only returned when the licence is issued or extended. */
  token?: string
}

export interface LicenceList {
  licences: Licence[]
  live: number
  total: number
  public_key: string
  /** Whether this server can provision subdomains, and the domain they hang off. */
  hosting: { available: boolean; domain: string }
}

/** This installation's own licence, when it is a copy running under one. */
export interface LicenceStatus {
  licensed: boolean
  state: LicenceState
  reason: string | null
  name?: string
  email?: string
  key_id?: string
  expires_on?: string
  days_left?: number
  /** Sold outright — no expiry. expires_on and days_left are null when true. */
  perpetual?: boolean
  blocked?: boolean
  /** No longer sent. The server stopped returning what a licensed copy validates
   *  and where: shown in Settings it read as the app reporting on its owner, and
   *  it put the publisher's own domain on a customer's screen. Kept optional so an
   *  older cached build that still reads them does not break. */
  reports?: Record<string, string>
  reports_to?: string
}

/** Text the app read out of a document, and the fields it implies. */
export interface DocSuggestions {
  ready: boolean
  has_text: boolean
  reason?: string
  /** Only fields that are still empty on the record — never overwrites your typing. */
  fields: Partial<{ expiry_date: string; issue_date: string; doc_number: string }>
  amounts?: number[]
  dates?: string[]
  text?: string
  preview?: string
}

/** An album the library implies, offered before anything is created. */
export interface AlbumSuggestion {
  name: string
  label: string
  confidence: number
  span: string
  count: number
  photo_ids: number[]
  cover_id: number
  cover_url: string | null
  exists: boolean
}

export interface SearchHit {
  id: number
  title: string
  sub?: string
  when?: string | null
  amount?: number
  route: string
  thumb_url?: string
  /** "text" = the words were found; "looks" = it merely resembles the query. */
  matched?: string
  /** True when the words were found only inside a scan, not in the title. */
  inside?: boolean
}

export interface SearchGroup { kind: string; label: string; count: number; items: SearchHit[] }

export interface SearchResults {
  query: string
  groups: SearchGroup[]
  total: number
  understood: { modules?: string[]; year?: number; month?: number }
}

/** A message sent to everyone running a copy of the app. */
/** Who a message reached. `audience` also carries "licence:L-XXXX" for a message
 *  sent to one customer, which is why it is a plain string. */
export interface BroadcastRecipient {
  key_id: string
  name: string
}

export interface BroadcastItem {
  id: number
  title: string
  body: string
  url: string | null
  kind: 'news' | 'update' | 'urgent'
  audience: string
  app_version: string | null
  delivered_local: number
  created_at: string | null
  resend_of: number | null
  /** Id of the later message that replaced this one, if it was sent again. A
   *  superseded message can never finish delivering — copies are handed only the
   *  newest of a lineage — so it reports no outstanding recipients. */
  superseded_by: number | null
  /** How many licensed copies this message is addressed to. */
  targets: number
  collected: BroadcastRecipient[]
  waiting: BroadcastRecipient[]
}
