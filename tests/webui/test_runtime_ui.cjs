const assert = require('node:assert/strict');
const test = require('node:test');
const createRuntime = require('../../module/webui/assets/js/runtime.js');

function fakeWindow({permission = 'default', secure = true, electron = false, enabled = false,
    embedded = false, unsupported = false} = {}) {
    const elements = [];
    class Element {
        constructor() { this.children = []; this.attributes = {}; this.style = {}; this.listeners = {}; this.textContent = ''; elements.push(this); }
        setAttribute(key, value) { this.attributes[key] = value; }
        appendChild(child) { this.children.push(child); }
        querySelector(selector) { return this.children.find(child => Object.hasOwn(child.attributes, selector.slice(1, -1))) || null; }
        addEventListener(event, callback) { this.listeners[event] = callback; }
    }
    const header = new Element();
    header.id = 'pywebio-scope-header_title';
    const controls = new Element();
    const listeners = {};
    const sent = [];
    const notifications = [];
    const storage = new Map(enabled ? [['aes-system-notifications-enabled', 'true']] : []);
    function Notification(title, args) { notifications.push({title, args}); }
    Notification.permission = permission;
    Notification.requestPermission = async () => { win.requestCount++; Notification.permission = win.permissionResult; return win.permissionResult; };
    const win = {
        document: {title: 'AES', createElement: () => new Element(),
            querySelectorAll: () => [controls], getElementById: id => elements.find(el => el.id === id) || null},
        navigator: {userAgent: electron ? 'Chrome Electron/30.0' : 'Chrome'},
        isSecureContext: secure, Notification: unsupported ? undefined : Notification,
        localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
        addEventListener: (name, fn) => { listeners[name] = fn; },
        removeEventListener: (name, fn) => { if (listeners[name] === fn) delete listeners[name]; },
        setInterval: fn => { win.tick = fn; return 1; }, clearInterval: () => {},
        WebIO: {_state: {CurrentSession: {on_session_close: fn => { win.disconnect = fn; }}}},
        requestCount: 0, permissionResult: 'granted',
    };
    win.parent = electron || embedded ? {postMessage: (message, origin) => sent.push({message, origin})} : win;
    const runtime = createRuntime(win, {electron, chinese: true});
    const ready = (origin = 'null', source = win.parent, supported = true) => listeners.message({
        source, origin, data: {source: 'aes-runtime-host', type: 'ready', notificationSupported: supported},
    });
    return {win, runtime, controls, sent, notifications, ready, listeners};
}
const running = {config_name: 'aes', state: 'running', completed: 2, total: 5, task: 'Mail', task_label: '收取邮件'};
const notice = {id: 7, title: '完成', content: '测试正文'};

test('real progress updates title and header; waiting restores title and page replacement works', () => {
    const {win, runtime} = fakeWindow();
    runtime.update(running, []);
    assert.equal(win.document.title, '[40%] AES');
    assert.equal(win.document.getElementById('aes-runtime-progress').textContent, '已处理 2/5 项任务 · 收取邮件');
    runtime.update({...running, total: null}, []);
    assert.equal(win.document.title, '[运行中] AES');
    runtime.update({...running, state: 'waiting'}, []);
    assert.equal(win.document.title, 'AES');
    runtime.stop();
});

test('browser stays unregistered until a direct enable click grants permission; duplicate notices display once', async () => {
    const {win, runtime, controls, notifications} = fakeWindow();
    assert.equal(runtime.clientKind(), null);
    runtime.update(running, [notice]);
    assert.equal(win.requestCount, 0);
    assert.equal(notifications.length, 0);
    await controls.querySelector('[data-aes-notification-enable]').listeners.click();
    assert.equal(win.requestCount, 1);
    assert.equal(runtime.clientKind(), 'browser');
    runtime.update(running, [notice, notice]);
    assert.equal(notifications.length, 1);
    win.Notification.permission = 'denied';
    assert.equal(runtime.clientKind(), null);
    runtime.update(running, [{...notice, id: 8}]);
    assert.equal(notifications.length, 1);
    runtime.stop();
});

test('existing browser grant alone does not opt in; remembered opt-in survives reload', () => {
    const first = fakeWindow({permission: 'granted'});
    assert.equal(first.runtime.clientKind(), null);
    const next = fakeWindow({permission: 'granted', enabled: true});
    assert.equal(next.runtime.clientKind(), 'browser');
    first.runtime.stop(); next.runtime.stop();
});

test('denied, insecure, unsupported and ordinary embedded browsers never request permission', async () => {
    for (const options of [{permission: 'denied'}, {secure: false}, {unsupported: true}, {embedded: true}]) {
        const {win, runtime} = fakeWindow(options);
        await runtime.enableNotifications();
        assert.equal(win.requestCount, 0);
        assert.equal(runtime.clientKind(), null);
        runtime.stop();
    }
});

test('untrusted parents cannot enable desktop handling or receive progress and notifications', () => {
    const browser = fakeWindow({embedded: true});
    browser.ready();
    browser.runtime.update(running, [notice]);
    assert.equal(browser.runtime.clientKind(), null);
    assert.equal(browser.sent.length, 0);
    const desktop = fakeWindow({electron: true});
    desktop.ready('https://attacker.example');
    desktop.ready('null', {});
    desktop.runtime.update(running, [notice]);
    assert.equal(desktop.runtime.clientKind(), null);
    assert.equal(desktop.sent.length, 0);
    browser.runtime.stop(); desktop.runtime.stop();
});

test('trusted desktop handshake routes notifications and progress without requesting browser permission', async () => {
    const {win, runtime, ready, sent, notifications} = fakeWindow({electron: true});
    ready();
    assert.equal(runtime.clientKind(), 'electron');
    await runtime.enableNotifications();
    runtime.update(running, [notice, notice]);
    assert.equal(win.requestCount, 0);
    assert.equal(notifications.length, 0);
    assert.equal(sent.filter(item => item.message.type === 'notification').length, 1);
    assert.equal(sent[0].origin, '*');
    win.disconnect();
    assert.equal(runtime.clientKind(), null);
    assert.equal(win.document.title, 'AES');
    assert.equal(sent.at(-1).message.progress.state, 'stopped');
});

test('local development host uses its precise origin and cannot change it later', () => {
    const {runtime, ready, sent} = fakeWindow({electron: true});
    ready('http://localhost:5173');
    runtime.update(running, []);
    assert.equal(sent[0].origin, 'http://localhost:5173');
    ready('http://127.0.0.1:5173');
    runtime.update(running, []);
    assert.equal(sent.at(-1).origin, 'http://localhost:5173');
    runtime.stop();
});

test('unsupported desktop notification capability does not claim a notification recipient', () => {
    const {runtime, ready, sent} = fakeWindow({electron: true});
    ready('null', undefined, false);
    assert.equal(runtime.clientKind(), null);
    runtime.update(running, [notice]);
    assert.equal(sent.filter(item => item.message.type === 'progress').length, 1);
    assert.equal(sent.filter(item => item.message.type === 'notification').length, 0);
    runtime.stop();
});

test('notification delivery errors disable the receiver and are visible to the user', async () => {
    const {win, runtime, controls} = fakeWindow({permission: 'granted', enabled: true});
    function BrokenNotification() { throw new Error('system unavailable'); }
    BrokenNotification.permission = 'granted';
    win.Notification = BrokenNotification;
    runtime.update(running, [notice]);
    assert.equal(runtime.clientKind(), null);
    assert.match(controls.querySelector('[data-aes-notification-status]').textContent, /发送失败/);
    runtime.stop();
});
