const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('one history block renders three dated events including acknowledgment', () => {
  const source = fs.readFileSync(path.join(__dirname, '../../src/staticfiles/subject/js/subject_validation_issue_modal.js'), 'utf8');
  const begin = source.indexOf('  function buildHistoryMessageNode(');
  const end = source.indexOf('\n  function loadHistory(', begin);
  class Node {
    constructor() { this.childNodes = []; this.textContent = ''; }
    appendChild(child) { this.childNodes.push(child); }
    setAttribute() {}
  }
  const context = {document: {createElement() { return new Node(); }}};
  vm.createContext(context);
  vm.runInContext(source.slice(begin, end), context);
  const messages = ['FAIL', 'Acknowledged', 'FAIL'].map((status, index) => ({dataset: {
    messageStatus: status, messageOpenedAt: `time ${index}`, messageOpenedBy: 'Tester',
    messageText: index === 1 ? '<script>acknowledged</script>' : 'Out of range', messageValue: '405',
  }}));
  const node = context.buildHistoryMessageNode({dataset: {historyLabel: 'Validation Issue #301', historyStatus: 'FAIL'},
    querySelectorAll() { return messages; }});
  const texts = node.childNodes.map(child => child.textContent);
  assert.deepEqual(texts.filter(text => text.startsWith('time ')),
    ['time 0 · FAIL · Tester', 'time 1 · Acknowledged · Tester', 'time 2 · FAIL · Tester']);
  assert.equal(texts.filter(text => text === 'Value: 405').length, 3);
  assert.ok(texts.includes('<script>acknowledged</script>'));
});

test('keyboard navigation remains inside validation dialog and Escape closes it', () => {
  const source = fs.readFileSync(path.join(__dirname, '../../src/staticfiles/subject/js/subject_validation_issue_modal.js'), 'utf8');
  const begin = source.indexOf("  document.addEventListener('keydown'");
  const document = {activeElement: null, addEventListener(type, handler) { this.handleKey = handler; }};
  const control = () => ({getClientRects() { return [{}]; }, focus() { document.activeElement = this; }});
  const first = control(), last = control();
  const modal = {hidden: false, querySelectorAll() { return [first, last]; }, contains(node) { return [first, last].includes(node); }};
  let closed = false;
  const context = {document, modal, closeModal() { closed = true; }};
  vm.createContext(context);
  vm.runInContext(source.slice(begin, source.lastIndexOf('\n})();')), context);
  const key = (key, shiftKey = false) => {
    let prevented = false;
    document.handleKey({key, shiftKey, preventDefault() { prevented = true; }});
    return prevented;
  };
  document.activeElement = last;
  assert.equal(key('Tab'), true);
  assert.equal(document.activeElement, first);
  assert.equal(key('Tab', true), true);
  assert.equal(document.activeElement, last);
  document.activeElement = {};
  key('Tab');
  assert.equal(document.activeElement, first);
  assert.equal(key('Escape'), true);
  assert.equal(closed, true);
});
