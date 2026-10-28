const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname,
  '../../../src/staticfiles/datacapture/js/subject-detail-behavior-runtime.js'), 'utf8');
// Exercise the actual expression runtime without initializing browser controls.
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('  function normalizeStatus('),
  source.indexOf('  function getFieldControls(')), context);
const age = (birth, date) => context.evalExpr(
  'age_years_between("birth", "date")', { birth, date }, null);

test('counts completed years before, on and after the birthday', () => {
  assert.equal(age('2003-10-29', '2026-10-28'), 22);
  assert.equal(age('2003-10-29', '2026-10-29'), 23);
  assert.equal(age('2003-10-29', '2026-10-30'), 23);
  assert.equal(age('2003-10-29', '2026-09-30'), 22);
  assert.equal(age('2003-10-29', '2026-11-01'), 23);
  assert.equal(age('2003-10-29', '2003-10-29'), 0);
});

test('supports date parts, split field references and literal dates', () => {
  assert.equal(age({ year: '2003', month: '10', day: '29' }, '2026-10-28'), 22);
  assert.equal(context.evalExpr('age_years_between("birth", "date")', {
    birth__year: '2003', birth__month: '10', birth__day: '29', date: '2026-10-29',
  }, null), 23);
  assert.equal(context.evalExpr('age_years_between("2003-10-29", "2026-10-28")', {}, null), 22);
});

test('uses calendar dates without timezone shifts', () => {
  assert.equal(age('2003-10-29', '2026-10-29T00:00:00+07:00'), 23);
});

test('Feb 29 birthday completes on March 1 in non-leap years', () => {
  assert.equal(age('2004-02-29', '2026-02-28'), 21);
  assert.equal(age('2004-02-29', '2026-03-01'), 22);
  assert.equal(age('2004-02-29', '2024-02-28'), 19);
  assert.equal(age('2004-02-29', '2024-02-29'), 20);
});

test('missing, partial and impossible dates do not produce an age', () => {
  for (const invalid of [null, '', '2003', '2003-10', '2023-02-29', '1900-02-29',
    '2003-04-31', '2003-00-29', '2003-10-00', { year: '2003' },
    { year: 2003, month: 10, day: '' }]) {
    assert.equal(age(invalid, '2026-10-28'), null);
    assert.equal(age('2003-10-29', invalid), null);
  }
  assert.equal(age('2000-02-29', '2026-03-01'), 26);
});
