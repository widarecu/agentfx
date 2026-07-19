/**
 * Vitest for the pure helpers backing `ValidatorOverrideModal`
 * (US-027).
 *
 * The React component itself is not rendered — the project's Vitest
 * config runs in the `node` environment with no
 * `@testing-library/react`. The submit-enabled gate and payload
 * builder are extracted as pure functions so the transition points
 * lock without a DOM.
 *
 * If jsdom + testing-library are added later, add a companion
 * `validatorOverrideModal.render.test.tsx` for the full render pass;
 * the pure-helper cases here stay valuable as fast smoke tests.
 */

import { describe, expect, it } from 'vitest';
import {
  REASON_MIN_LENGTH,
  _buildOverridePayload,
  _canSubmit,
} from '../../src/components/ValidatorOverrideModal';

describe('_canSubmit — submit-button gate', () => {
  it('is false when the reason is empty', () => {
    expect(_canSubmit('', false)).toBe(false);
  });

  it('is false when the reason is only whitespace (trims before checking)', () => {
    expect(_canSubmit('   '.repeat(20), false)).toBe(false);
  });

  it('is false when the reason is one character below the minimum', () => {
    const almostEnough = 'x'.repeat(REASON_MIN_LENGTH - 1);
    expect(_canSubmit(almostEnough, false)).toBe(false);
  });

  it('is true when the reason exactly meets the minimum length', () => {
    const exact = 'x'.repeat(REASON_MIN_LENGTH);
    expect(_canSubmit(exact, false)).toBe(true);
  });

  it('is true when the reason comfortably exceeds the minimum', () => {
    const plenty = 'x'.repeat(REASON_MIN_LENGTH * 3);
    expect(_canSubmit(plenty, false)).toBe(true);
  });

  it('trims leading/trailing whitespace before applying the length check', () => {
    // 20 chars of trailing whitespace after an under-minimum core
    // must NOT enable the submit.
    const paddedTooShort = '  short  ';
    expect(_canSubmit(paddedTooShort, false)).toBe(false);

    // Padding a valid reason with whitespace still trims to a valid
    // length.
    const paddedValid = `   ${'x'.repeat(REASON_MIN_LENGTH)}   `;
    expect(_canSubmit(paddedValid, false)).toBe(true);
  });

  it('is always false while a submit is in flight, even with a valid reason', () => {
    const valid = 'x'.repeat(REASON_MIN_LENGTH);
    expect(_canSubmit(valid, true)).toBe(false);
  });
});

describe('_buildOverridePayload — request-body shape', () => {
  it("maps newValue='true' to new_value: true", () => {
    expect(
      _buildOverridePayload('true', 'Unblocking demo — reviewed by trader'),
    ).toEqual({
      new_value: true,
      reason: 'Unblocking demo — reviewed by trader',
    });
  });

  it("maps newValue='false' to new_value: false", () => {
    expect(_buildOverridePayload('false', 'Rejecting per Wilson')).toEqual({
      new_value: false,
      reason: 'Rejecting per Wilson',
    });
  });

  it('trims leading/trailing whitespace from the reason', () => {
    // The audit log stores exactly what we ship — sending pre-
    // trimmed text keeps the DecisionLog reason clean.
    expect(
      _buildOverridePayload(
        'true',
        '   Unblocking paper-trading walkthrough   ',
      ),
    ).toEqual({
      new_value: true,
      reason: 'Unblocking paper-trading walkthrough',
    });
  });

  it('preserves internal whitespace and unicode', () => {
    const reason =
      'Diagnostic agent will re-evaluate this genome — v0.5.0 follow-up.';
    expect(_buildOverridePayload('true', reason)).toEqual({
      new_value: true,
      reason,
    });
  });
});
