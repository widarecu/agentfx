/**
 * Vitest for the `/paper/approvals/:genomeId` deep-link focus (AFX-38).
 *
 * The PM morning-brief email links straight to the approval row of the
 * genome it recommends. The queue often holds several candidates for
 * the same pair/granularity (2026-10-27: 5+ AUD_USD M30 rows), so the
 * linked row is pinned first and highlighted. `_focusApprovalItems` is
 * the pure half of that — locked here without a jsdom render pass.
 */

import { describe, expect, it } from 'vitest';
import { _focusApprovalItems } from '../../src/pages/ApprovalQueue';
import type { ApprovalQueueItem } from '../../src/types/paper';

function item(id: string): ApprovalQueueItem {
  return {
    strategy_genome_id: id,
    run_id: id,
    instrument: 'AUD_USD',
    granularity: 'M30',
    fitness: 1.2,
    metrics_summary: null,
    created_at: null,
    dsl_exit: null,
    imported_from_email: null,
  };
}

const A = 'genome_gen_evorun_20261027_ab12cd34_003_017';
const B = 'genome_gen_evorun_20261027_ab12cd34_003_004';
const C = 'genome_gen_evorun_20261027_ab12cd34_002_011';

const ids = (items: ApprovalQueueItem[]) =>
  items.map((i) => i.strategy_genome_id);

describe('_focusApprovalItems', () => {
  it('no deep link → queue order untouched, nothing focused', () => {
    const items = [item(B), item(A), item(C)];
    const { ordered, focusFound } = _focusApprovalItems(items, undefined);
    expect(ids(ordered)).toEqual([B, A, C]);
    expect(focusFound).toBe(false);
  });

  it('linked genome is pinned first; the rest keep their order', () => {
    const items = [item(B), item(C), item(A)];
    const { ordered, focusFound } = _focusApprovalItems(items, A);
    expect(ids(ordered)).toEqual([A, B, C]);
    expect(focusFound).toBe(true);
  });

  it('already-first genome stays first', () => {
    const { ordered, focusFound } = _focusApprovalItems([item(A), item(B)], A);
    expect(ids(ordered)).toEqual([A, B]);
    expect(focusFound).toBe(true);
  });

  it('linked genome not on the queue (deployed / rejected / not yours) → focusFound false', () => {
    const items = [item(B), item(C)];
    const { ordered, focusFound } = _focusApprovalItems(items, A);
    expect(ids(ordered)).toEqual([B, C]);
    expect(focusFound).toBe(false);
  });

  it('matches the full id only — a shared prefix is not a match', () => {
    // Every genome minted in one run shares a long prefix; a truncated
    // id must never focus some arbitrary sibling.
    const { focusFound } = _focusApprovalItems(
      [item(A), item(B)],
      'genome_gen_evorun_20261027_ab12cd34_003_0',
    );
    expect(focusFound).toBe(false);
  });

  it('does not mutate the input array', () => {
    const items = [item(B), item(A)];
    _focusApprovalItems(items, A);
    expect(ids(items)).toEqual([B, A]);
  });
});
