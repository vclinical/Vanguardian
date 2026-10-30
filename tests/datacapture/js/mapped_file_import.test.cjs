const assert = require('node:assert/strict');
const { test } = require('node:test');
const { displayValue, fieldValue, mayApply, requestBody, localize } = require('../../../src/staticfiles/datacapture/js/mapped-file-import.js');
const ready = { status: 'preview', token: 'signed', can_apply: true, errors: [], overwrite_count: 0 };

test('zero and false remain visible rather than appearing as missing data', () => {
  assert.equal(displayValue(0), '0');
  assert.equal(displayValue(false), 'false');
  assert.equal(displayValue(null), '—');
  assert.equal(displayValue(''), '—');
});
test('general confirmation is required even when filling empty fields', () => {
  assert.equal(mayApply(ready, false, false, false), false);
  assert.equal(mayApply(ready, true, false, false), true);
});
test('overwrites require separate affirmative confirmation', () => {
  const overwrite = { ...ready, overwrite_count: 1 };
  assert.equal(mayApply(overwrite, true, false, false), false);
  assert.equal(mayApply(overwrite, true, true, false), true);
  assert.equal(mayApply(overwrite, false, true, false), false);
});
test('no apply for unresolved selection, errors, invalidated token, or active request', () => {
  [null, { ...ready, status: 'selection_required' }, { ...ready, token: '' },
    { ...ready, can_apply: false }, { ...ready, errors: ['Locked'] }].forEach((value) => {
    assert.equal(mayApply(value, true, true, false), false);
  });
  assert.equal(mayApply(ready, true, true, true), false);
});
test('apply sends signed preview and explicit decisions, never editable clinical payload', () => {
  assert.deepEqual(requestBody(ready, false), { token: 'signed', confirmed: true, confirm_overwrite: false });
  assert.deepEqual(requestBody(ready, true), { token: 'signed', confirmed: true, confirm_overwrite: true });
});

test('choice comparisons show both approved label and stored code', () => {
  assert.equal(fieldValue('0', [{ value: '0', label: 'Negative' }]), 'Negative (0)');
  assert.equal(fieldValue('unknown', [{ value: '0', label: 'Negative' }]), 'unknown');
});

test('localized messages interpolate named placeholders without losing zero or false', () => {
  assert.equal(localize({ row: 'Row {number}: {value}' }, 'row', { number: 0, value: false }), 'Row 0: false');
  assert.equal(localize({ row: 'Dòng {number}: {value}' }, 'row', { number: 5, value: 'Âm tính' }), 'Dòng 5: Âm tính');
  assert.equal(localize({ raw: 'In file: {value}' }, 'raw', { value: '<script>$&{other}</script>' }), 'In file: <script>$&{other}</script>');
});

test('every dynamic message has a Django-translated template attribute', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const source = fs.readFileSync(path.resolve(__dirname, '../../../src/staticfiles/datacapture/js/mapped-file-import.js'), 'utf8');
  const template = fs.readFileSync(path.resolve(__dirname, '../../../src/templates/subject/includes/mapped_file_import.html'), 'utf8');
  const keys = [...source.matchAll(/\bt\('([^']+)'/g)].map((match) => match[1]);
  assert.ok(keys.length > 25);
  keys.forEach((key) => {
    const attribute = key.replace(/[A-Z]/g, (letter) => '-' + letter.toLowerCase());
    assert.ok(template.includes('data-' + attribute + '="{% trans '), 'Missing translation for ' + key);
  });
});
