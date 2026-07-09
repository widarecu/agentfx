/**
 * Vitest for the empty-state banner scenario picker in
 * `/paper/approvals` (US-027).
 *
 * The React component is a straight visual translation of
 * `_pickEmptyBannerScenario`. Locking the pure classifier here means
 * we can trust the four visual states without a jsdom render pass.
 * Wilson's 2026-07-08 smoke-test misdiagnosis was the whole reason
 * this classification exists — mixing scenarios is what turned "0
 * passed" into "validator is broken" in his mental model.
 */

import { describe, expect, it } from 'vitest';
import { _pickEmptyBannerScenario } from '../../src/pages/ApprovalQueue';
import type { ApprovalQueueMeta } from '../../src/types/paper';

function meta(partial: Partial<ApprovalQueueMeta> = {}): ApprovalQueueMeta {
  return {
    evolution_run_count: 0,
    total_genomes: 0,
    validated_count: 0,
    passed_count: 0,
    touched_count: 0,
    closest_to_passing: null,
    ...partial,
  };
}

describe('_pickEmptyBannerScenario', () => {
  it('null meta → no_evolutions', () => {
    expect(_pickEmptyBannerScenario(null)).toBe('no_evolutions');
  });

  it('zero evolutions → no_evolutions', () => {
    expect(_pickEmptyBannerScenario(meta())).toBe('no_evolutions');
  });

  it('evolutions ran but zero validated → none_validated', () => {
    expect(
      _pickEmptyBannerScenario(
        meta({ evolution_run_count: 3, total_genomes: 900, validated_count: 0 }),
      ),
    ).toBe('none_validated');
  });

  it("Wilson's UAT reality: 17 runs, 255 validated, 0 passed → none_passed", () => {
    expect(
      _pickEmptyBannerScenario(
        meta({
          evolution_run_count: 17,
          total_genomes: 8730,
          validated_count: 255,
          passed_count: 0,
        }),
      ),
    ).toBe('none_passed');
  });

  it('passing genomes exist but user deployed all → all_deployed', () => {
    expect(
      _pickEmptyBannerScenario(
        meta({
          evolution_run_count: 1,
          total_genomes: 500,
          validated_count: 15,
          passed_count: 3,
          touched_count: 3,
        }),
      ),
    ).toBe('all_deployed');
  });

  it('none_validated takes precedence over none_passed', () => {
    // Defensive — if the backend ever returns validated_count=0 with
    // passed_count>0 (impossible today), the "validator never ran"
    // banner is the more accurate diagnosis.
    expect(
      _pickEmptyBannerScenario(
        meta({
          evolution_run_count: 1,
          total_genomes: 100,
          validated_count: 0,
          passed_count: 0,
        }),
      ),
    ).toBe('none_validated');
  });
});
