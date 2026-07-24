/**
 * Vitest for `_overlayReasonMessage` — PR 6 of the LivePnLChart
 * redesign. Locks the human-readable copy for every known
 * `BacktestOverlayReason` so a future edit to the wording is a
 * deliberate change with an obvious test failure, not silent drift.
 */

import { describe, expect, it } from 'vitest';
import { _overlayReasonMessage } from '../../src/components/LivePnLChart';

describe('_overlayReasonMessage', () => {
  it('explains the "no linked evolution" reason', () => {
    const msg = _overlayReasonMessage('no_associated_evolution_run');
    expect(msg).toMatch(/isn't linked to an evolution/);
  });

  it('explains the "no OOS folds" reason', () => {
    const msg = _overlayReasonMessage('no_oos_equity_persisted');
    expect(msg).toMatch(/No OOS folds/);
  });

  it('explains the v0.4.0 schema gap in human terms', () => {
    const msg = _overlayReasonMessage('oos_equity_series_not_persisted');
    expect(msg).toMatch(/OOS equity-series column/);
    expect(msg).toMatch(/v0\.4\.0/);
  });

  it('falls back to a generic message for null / unknown reasons', () => {
    expect(_overlayReasonMessage(null)).toMatch(/no data/);
    // Unknown reason string routes to the default branch too.
    expect(
      _overlayReasonMessage('some_future_reason' as unknown as null),
    ).toMatch(/no data/);
  });
});
