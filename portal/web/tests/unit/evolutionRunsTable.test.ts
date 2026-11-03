/**
 * AFX-37: Evolution runs table shows, per run, how many genomes cleared
 * the walk-forward validator and the best fitness the run reached, and
 * both columns are sortable.
 *
 * The table itself is a straight visual translation of the pure helpers
 * below (same approach as `_pickEmptyBannerScenario` /
 * `selectPoolColumnView`), so we pin those without a jsdom render.
 * `listEvolutionRuns` is tested against a stubbed fetch to lock the
 * `sort_by` / `sort_order` query contract with the backend. Sorting is
 * server-side because the list is paginated; a per-page sort would hide
 * a winner sitting on page 4.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_RUN_SORT,
  ariaSortFor,
  nextRunSort,
  selectValidatorPassedView,
} from '../../src/pages/EvolutionRuns';
import { formatFitness, listEvolutionRuns } from '../../src/services/evolutionApi';

describe('nextRunSort', () => {
  it('defaults to newest first (unchanged list behaviour)', () => {
    expect(DEFAULT_RUN_SORT).toEqual({ field: 'created_at', order: 'desc' });
  });

  it('a newly-clicked column sorts descending first (best / most passes on top)', () => {
    expect(nextRunSort(DEFAULT_RUN_SORT, 'best_fitness')).toEqual({
      field: 'best_fitness',
      order: 'desc',
    });
    expect(nextRunSort(DEFAULT_RUN_SORT, 'validator_passed_count')).toEqual({
      field: 'validator_passed_count',
      order: 'desc',
    });
  });

  it('clicking the active column flips the direction', () => {
    const desc = nextRunSort(DEFAULT_RUN_SORT, 'best_fitness');
    const asc = nextRunSort(desc, 'best_fitness');
    expect(asc).toEqual({ field: 'best_fitness', order: 'asc' });
    expect(nextRunSort(asc, 'best_fitness')).toEqual({
      field: 'best_fitness',
      order: 'desc',
    });
  });

  it('switching columns resets to descending', () => {
    const fitnessAsc = { field: 'best_fitness', order: 'asc' } as const;
    expect(nextRunSort(fitnessAsc, 'validator_passed_count')).toEqual({
      field: 'validator_passed_count',
      order: 'desc',
    });
  });

  it('clicking Created on the default sort flips to oldest first', () => {
    expect(nextRunSort(DEFAULT_RUN_SORT, 'created_at')).toEqual({
      field: 'created_at',
      order: 'asc',
    });
  });
});

describe('ariaSortFor', () => {
  it('reports the active column direction and "none" elsewhere', () => {
    const sort = { field: 'best_fitness', order: 'desc' } as const;
    expect(ariaSortFor(sort, 'best_fitness')).toBe('descending');
    expect(ariaSortFor({ ...sort, order: 'asc' }, 'best_fitness')).toBe('ascending');
    expect(ariaSortFor(sort, 'validator_passed_count')).toBe('none');
    expect(ariaSortFor(sort, 'created_at')).toBe('none');
  });
});

describe('selectValidatorPassedView', () => {
  it('highlights a run with validator-passing genomes so it stands out without clicking in', () => {
    const view = selectValidatorPassedView(1, 'completed');
    expect(view.highlighted).toBe(true);
    expect(view.label).toBe('1');
    expect(view.title).toMatch(/1 genome in this run cleared the walk-forward validator/);
  });

  it('pluralises the tooltip', () => {
    const view = selectValidatorPassedView(3, 'completed');
    expect(view.highlighted).toBe(true);
    expect(view.label).toBe('3');
    expect(view.title).toMatch(/3 genomes in this run cleared/);
  });

  it('shows a muted 0 for a finished run with no passes', () => {
    for (const status of ['completed', 'stopped', 'failed'] as const) {
      const view = selectValidatorPassedView(0, status);
      expect(view.highlighted).toBe(false);
      expect(view.label).toBe('0');
    }
  });

  it('shows "—" (not a verdict) while validation has not run yet', () => {
    // The validator only runs after the final generation, so 0 on a
    // live run means "pending", not "failed".
    for (const status of ['queued', 'running'] as const) {
      const view = selectValidatorPassedView(0, status);
      expect(view.highlighted).toBe(false);
      expect(view.label).toBe('—');
      expect(view.title).toMatch(/after the final generation/);
    }
  });

  it('still highlights passes on a live run (e.g. an ADMIN override)', () => {
    expect(selectValidatorPassedView(2, 'running').highlighted).toBe(true);
  });
});

describe('formatFitness', () => {
  it('uses 3 decimals, matching the PM brief ("fitness 1.264")', () => {
    expect(formatFitness(1.2641)).toBe('1.264');
    expect(formatFitness(1.264)).toBe('1.264');
    expect(formatFitness(0)).toBe('0.000');
    expect(formatFitness(-0.4567)).toBe('-0.457');
  });

  it('renders missing or non-finite values as an em dash', () => {
    expect(formatFitness(null)).toBe('—');
    expect(formatFitness(Number.NEGATIVE_INFINITY)).toBe('—');
    expect(formatFitness(Number.NaN)).toBe('—');
  });
});

describe('listEvolutionRuns sort params', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubFetch() {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ items: [], total: 0, page: 1, page_size: 10 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    );
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('localStorage', { getItem: () => null });
    return fetchMock;
  }

  function requestedParams(fetchMock: ReturnType<typeof stubFetch>): URLSearchParams {
    const url = String(fetchMock.mock.calls[0][0]);
    return new URL(url, 'http://localhost').searchParams;
  }

  it('sends sort_by / sort_order when a sort is requested', async () => {
    const fetchMock = stubFetch();
    await listEvolutionRuns(2, 10, {
      scope: 'all',
      sortBy: 'best_fitness',
      sortOrder: 'asc',
    });
    const params = requestedParams(fetchMock);
    expect(params.get('sort_by')).toBe('best_fitness');
    expect(params.get('sort_order')).toBe('asc');
    expect(params.get('page')).toBe('2');
    expect(params.get('scope')).toBe('all');
  });

  it('omits sort params when none is requested (backend default = newest first)', async () => {
    const fetchMock = stubFetch();
    await listEvolutionRuns(1, 10);
    const params = requestedParams(fetchMock);
    expect(params.has('sort_by')).toBe(false);
    expect(params.has('sort_order')).toBe(false);
  });
});
