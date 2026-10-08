const STATE_TONE = {
  NEW: 'grey', INVESTIGATED: 'blue', DRY_RUN: 'blue', PENDING: 'amber', APPROVED: 'amber', EXECUTED: 'green', VERIFIED: 'green',
  REJECTED: 'red', EXPIRED: 'red', REFUSED: 'red', POLICY_INVALID: 'red', EXECUTION_FAILED: 'red', VERIFICATION_FAILED: 'red', DISMISSED: 'grey',
};
export const Chip = ({ state, tone }) => <span className={`chip chip-${tone || STATE_TONE[state] || 'grey'}`}>{state}</span>;
export const usd = (v) => (v == null ? '—' : Number(v).toLocaleString(undefined, { style: 'currency', currency: 'USD' }));
