/**
 * TypeScript mirror of `src/agentfx/api/schemas/user_credentials.py` (US-019).
 *
 * Field names use the API's snake_case verbatim so JSON deserializes
 * without a mapping layer. The public views never carry the plaintext
 * API key — the frontend has no reason to see it.
 */

export type OandaEnvironment = 'practice' | 'live';

export interface UserOandaCredentialsPublic {
  environment: string;
  verified: boolean;
  verified_at: string | null;
  verify_failure_reason: string | null;
  key_version: number;
  /** AFX-7: when true, the fleet may lease the caller's OANDA sub-
   *  accounts for its own paper experiments. Flipped from Settings. */
  allow_fleet_pool_use: boolean;
  updated_at: string | null;
}

export type FleetPoolStatus = 'free' | 'in_use' | 'cooling_down' | 'disabled';

export interface UserOandaAccountPublic {
  oanda_account_id: string;
  alias: string | null;
  currency: string;
  last_seen_at: string;
  /** v0.7.x: set to the caller's APPROVED or RUNNING paper run's id
   *  on this account, if any; null otherwise. */
  active_paper_run_id: string | null;
  /** AFX-7 fleet-pool state. Only meaningful when the parent credentials
   *  have `allow_fleet_pool_use=true`. */
  pool_status: FleetPoolStatus;
  pool_current_run_id: string | null;
  pool_in_use_since: string | null;
  pool_cooling_until: string | null;
}

export interface UserOandaCredentialsSettingsPatch {
  /** Omit to leave unchanged. */
  allow_fleet_pool_use?: boolean;
}

export interface UserOandaAccountsList {
  items: UserOandaAccountPublic[];
  verified_at: string | null;
}

export interface VerifyCredentialsResponse {
  verified: boolean;
  verified_at: string | null;
  verify_failure_reason: string | null;
  accounts: UserOandaAccountPublic[];
}

export interface UserOandaCredentialsUpsert {
  oanda_api_key: string;
  environment?: OandaEnvironment;
}

/** UI state discriminant — three states the /settings page renders. */
export type CredentialsState =
  | { kind: 'loading' }
  | { kind: 'empty' } // No credentials set yet.
  | { kind: 'unverified'; credentials: UserOandaCredentialsPublic }
  | {
      kind: 'verified';
      credentials: UserOandaCredentialsPublic;
      accounts: UserOandaAccountPublic[];
    };
