import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {test} from 'node:test';
import {
  isRuntimeIpcSender,
  parseRuntimeFrameMessage,
  parseRuntimeNotification,
  parseRuntimeProgress,
} from '../packages/common/runtime.ts';
import {createRuntimeController} from '../packages/main/src/runtimeController.ts';

const running = {
  config_name: 'test',
  state: 'running',
  completed: 1,
  total: 4,
  task: 'Daily',
};
const notice = {id: '1', title: 'Task stopped', content: 'Please check the application.'};

function desktop() {
  const calls = [];
  const notices = [];
  const warnings = [];
  const state = {destroyed: false, minimized: true, supported: true};
  const window = {
    isDestroyed: () => state.destroyed,
    setProgressBar: (...args) => calls.push(['progress', ...args]),
    isMinimized: () => state.minimized,
    restore: () => calls.push(['restore']),
    show: () => calls.push(['show']),
    focus: () => calls.push(['focus']),
  };
  const provider = {
    isSupported: () => state.supported,
    create: payload => {
      const native = new EventEmitter();
      native.show = () => calls.push(['notification', payload]);
      native.close = () => {
        calls.push(['closeNotification']);
        native.emit('close');
      };
      notices.push(native);
      return native;
    },
  };
  const controller = createRuntimeController(window, provider, message => warnings.push(message));
  return {controller, calls, notices, warnings, state, provider};
}

test('accepts valid counters and unknown totals without inventing a percentage', () => {
  assert.deepEqual(parseRuntimeProgress(running), running);
  assert.deepEqual(parseRuntimeProgress({...running, total: null}), {...running, total: null});
  assert.deepEqual(parseRuntimeProgress({...running, total: 0}), {...running, total: 0});
});

test('rejects invalid progress values instead of forwarding them to the native API', () => {
  for (const completed of [NaN, Infinity, -1, 0.5, Number.MAX_SAFE_INTEGER + 1, '1', null]) {
    assert.equal(parseRuntimeProgress({...running, completed}), null);
  }
  for (const total of [NaN, Infinity, -1, 0.5, '4', undefined]) {
    assert.equal(parseRuntimeProgress({...running, total}), null);
  }
  for (const state of ['invalid', ['running'], {}, null]) {
    assert.equal(parseRuntimeProgress({...running, state}), null);
  }
  assert.equal(parseRuntimeProgress({...running, completed: 5}), null);
  assert.equal(parseRuntimeProgress({...running, task: 'x'.repeat(257)}), null);
  assert.equal(parseRuntimeProgress({...running, config_name: 'x'.repeat(129)}), null);
});

test('accepts numeric notification IDs and bounds all desktop notification fields', () => {
  assert.deepEqual(parseRuntimeNotification({...notice, id: 1}), notice);
  for (const id of [0, -1, Infinity, 1.1, '', ' ', 'x'.repeat(129), {}, null]) {
    assert.equal(parseRuntimeNotification({...notice, id}), null);
  }
  assert.equal(parseRuntimeNotification({...notice, title: 'x'.repeat(257)}), null);
  assert.equal(parseRuntimeNotification({...notice, content: 'x'.repeat(4097)}), null);
  assert.equal(parseRuntimeNotification({...notice, content: {html: '<b>text</b>'}}), null);
});

test('requires both the configured WebUI origin and the current iframe window', () => {
  const frame = {};
  const data = {source: 'aes-runtime', type: 'progress', progress: running};
  const event = {source: frame, origin: 'http://127.0.0.1:22267', data};
  assert.deepEqual(
    parseRuntimeFrameMessage(event, frame, 'http://127.0.0.1:22267/?theme=dark'),
    data,
  );
  assert.equal(
    parseRuntimeFrameMessage({...event, source: {}}, frame, 'http://127.0.0.1:22267'),
    null,
  );
  assert.equal(
    parseRuntimeFrameMessage(
      {...event, origin: 'http://127.0.0.1:22268'},
      frame,
      'http://127.0.0.1:22267',
    ),
    null,
  );
  assert.equal(parseRuntimeFrameMessage(event, null, 'http://127.0.0.1:22267'), null);
  assert.equal(parseRuntimeFrameMessage(event, frame, 'file:///renderer/index.html'), null);
  assert.equal(parseRuntimeFrameMessage(event, frame, 'invalid-url'), null);
  assert.equal(
    parseRuntimeFrameMessage(
      {...event, data: {...data, source: 'other'}},
      frame,
      'http://127.0.0.1:22267',
    ),
    null,
  );
  assert.equal(
    parseRuntimeFrameMessage(
      {...event, data: {...data, type: 'unknown'}},
      frame,
      'http://127.0.0.1:22267',
    ),
    null,
  );
});

test('IPC accepts only the main window, its top frame, and the original renderer document', () => {
  const rendererUrl = 'file:///D:/App/packages/renderer/dist/index.html';
  const frame = {url: rendererUrl + '#/Alas'};
  const contents = {mainFrame: frame, isDestroyed: () => false};
  const event = {sender: contents, senderFrame: frame};
  assert.equal(isRuntimeIpcSender(event, contents, rendererUrl), true);
  assert.equal(isRuntimeIpcSender({...event, sender: {}}, contents, rendererUrl), false);
  assert.equal(
    isRuntimeIpcSender({...event, senderFrame: {url: frame.url}}, contents, rendererUrl),
    false,
  );
  assert.equal(isRuntimeIpcSender({...event, senderFrame: null}, contents, rendererUrl), false);
  frame.url = 'https://example.com/#/Alas';
  assert.equal(isRuntimeIpcSender(event, contents, rendererUrl), false);
  frame.url = rendererUrl;
  contents.isDestroyed = () => true;
  assert.equal(isRuntimeIpcSender(event, contents, rendererUrl), false);
});

test('maps real work to determinate/indeterminate/error native modes and clears terminal states', () => {
  const {controller, calls} = desktop();
  controller.setProgress(running, 0);
  controller.setProgress({...running, total: null}, 1);
  controller.setProgress({...running, state: 'error', completed: 0}, 2);
  controller.setProgress({...running, state: 'waiting'}, 3);
  assert.deepEqual(calls, [
    ['progress', 0.25, {mode: 'normal'}],
    ['progress', 2, {mode: 'indeterminate'}],
    ['progress', 1, {mode: 'error'}],
    ['progress', -1],
  ]);
  for (const state of ['stopped', 'completed']) {
    controller.setProgress(running, 4);
    controller.setProgress({...running, state}, 5);
    assert.deepEqual(calls.at(-1), ['progress', -1]);
  }
});

test('unchanged heartbeats keep long tasks alive without repeating native progress calls', () => {
  const {controller, calls} = desktop();
  controller.setProgress(running, 0);
  controller.setProgress(running, 10000);
  controller.expireProgress(20000);
  assert.equal(calls.length, 1);
  controller.expireProgress(25000);
  assert.deepEqual(calls.at(-1), ['progress', -1]);
  controller.expireProgress(40000);
  assert.equal(calls.length, 2);
});

test('deduplicates notifications across renderer reloads and restores the window on click', () => {
  const {controller, calls, notices} = desktop();
  assert.equal(controller.notify(notice), true);
  controller.clearProgress();
  assert.equal(controller.notify({...notice}), true);
  assert.equal(notices.length, 1);
  notices[0].emit('click');
  assert.deepEqual(calls.slice(-3), [['restore'], ['show'], ['focus']]);
});

test('unsupported and failed notifications are logged without throwing', () => {
  const {controller, state, warnings, notices, provider} = desktop();
  state.supported = false;
  assert.equal(controller.notify(notice), false);
  assert.equal(notices.length, 0);
  state.supported = true;
  assert.equal(controller.notify(notice), true);
  notices[0].emit('failed', {}, 'Windows rejected the notification');
  assert.match(warnings.at(-1), /Windows rejected/);
  provider.isSupported = () => {
    throw new Error('support check failed');
  };
  assert.equal(controller.notificationSupported(), false);
  assert.match(warnings.at(-1), /support check failed/);
});

test('creation and showing failures do not crash the app or suppress a later retry', () => {
  const {controller, provider, warnings} = desktop();
  const create = provider.create;
  provider.create = () => {
    throw new Error('create failed');
  };
  assert.equal(controller.notify(notice), false);
  assert.match(warnings.at(-1), /create failed/);
  provider.create = payload => {
    const native = create(payload);
    native.show = () => {
      throw new Error('show failed');
    };
    return native;
  };
  assert.equal(controller.notify(notice), false);
  assert.match(warnings.at(-1), /show failed/);
  provider.create = create;
  assert.equal(controller.notify(notice), true);
});

test('destroyed windows reject progress, notifications and late notification clicks', () => {
  const {controller, state, calls, notices} = desktop();
  controller.notify(notice);
  calls.length = 0;
  state.destroyed = true;
  assert.equal(controller.setProgress(running), false);
  assert.equal(controller.notify({...notice, id: '2'}), false);
  assert.equal(controller.notificationSupported(), false);
  notices[0].emit('click');
  controller.clearProgress();
  assert.deepEqual(calls, []);
});

test('disposing clears progress, closes notifications and rejects later work', () => {
  const {controller, calls, notices} = desktop();
  controller.setProgress(running);
  controller.notify(notice);
  controller.dispose();
  assert.deepEqual(calls.slice(-2), [['progress', -1], ['closeNotification']]);
  const before = calls.length;
  notices[0].emit('click');
  controller.dispose();
  assert.equal(controller.notify({...notice, id: '2'}), false);
  assert.equal(controller.setProgress(running), false);
  assert.equal(calls.length, before);
});

test('notification identity preparation stays lazy and blocks sending on a failed registration', () => {
  const {controller, provider, notices} = desktop();
  let preparations = 0;
  provider.ensureReady = () => {
    preparations += 1;
    return false;
  };
  assert.equal(controller.notificationSupported(), true);
  assert.equal(preparations, 0);
  assert.equal(controller.notify(notice), false);
  assert.equal(preparations, 1);
  assert.equal(notices.length, 0);
  provider.ensureReady = () => {
    preparations += 1;
    return true;
  };
  assert.equal(controller.notify(notice), true);
  assert.equal(controller.notify(notice), true);
  assert.equal(preparations, 2);
  assert.equal(notices.length, 1);
});
