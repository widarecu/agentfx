import { describe, expect, it } from 'vitest';
import {
  describePaperLossHalt,
  PAPER_LOSS_HALT_LABEL,
  PAPER_NO_LOSS_HALT_NOTE,
} from '../../src/utils/paperLossHalt';

describe('describePaperLossHalt (AFX-104)', () => {
  it('reports margin call only when risk_config is missing', () => {
    expect(describePaperLossHalt(null)).toBe(PAPER_LOSS_HALT_LABEL);
    expect(describePaperLossHalt(undefined)).toBe(PAPER_LOSS_HALT_LABEL);
  });

  it('reports margin call only for new runs without legacy caps', () => {
    expect(describePaperLossHalt({ stop_loss_pips: 20 })).toBe(
      PAPER_LOSS_HALT_LABEL,
    );
    expect(
      describePaperLossHalt({ loss_cap_pct: null, kill_switch_dd_pct: null }),
    ).toBe(PAPER_LOSS_HALT_LABEL);
  });

  it('marks stored legacy caps as ignored instead of active thresholds', () => {
    const text = describePaperLossHalt({
      loss_cap_pct: 2,
      kill_switch_dd_pct: 5,
    });
    expect(text).toBe(`${PAPER_LOSS_HALT_LABEL}; old 2% / 5% cap ignored`);
    expect(text).not.toMatch(/soft halt|hard halt|near halt/i);
  });

  it('handles a legacy run with only one cap stored', () => {
    expect(describePaperLossHalt({ loss_cap_pct: 2 })).toBe(
      `${PAPER_LOSS_HALT_LABEL}; old 2% cap ignored`,
    );
  });

  it('deploy note names the remaining stop conditions', () => {
    expect(PAPER_NO_LOSS_HALT_NOTE).toMatch(/no loss cap/);
    expect(PAPER_NO_LOSS_HALT_NOTE).toMatch(/margin call/);
  });
});
