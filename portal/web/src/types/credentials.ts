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
  updated_at: string | null;
}

export interface UserOandaAccountPublic {
  oanda_account_id: string;
  alias: string | null;
  currency: string;
  last_seen_at: string;
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
