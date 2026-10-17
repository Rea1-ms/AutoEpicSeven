/* Progress and system notifications survive PyWebIO page changes. */
(function (root, createRuntime) {
    if (typeof module === 'object' && module.exports) {
        module.exports = createRuntime;
    } else {
        if (root.aesRuntime) root.aesRuntime.stop();
        root.aesRuntime = createRuntime(root, runtimeOptions);
    }
}(typeof window === 'undefined' ? null : window, function (win, options) {
    'use strict';
    const doc = win.document;
    const originalTitle = doc.title;
    const text = (zh, en) => options.chinese ? zh : en;
    const storageKey = 'aes-system-notifications-enabled';
    let enabled = false;
    let closed = false;
    let permissionPending = false;
    let notificationFailed = false;
    let hostOrigin = null;
    let hostSeen = 0;
    let hostSupported = false;
    let lastUpdate = Date.now();
    let currentProgress = null;
    const seenNotifications = new Set();
    const desktop = Boolean(options.electron && /Electron\//.test(win.navigator.userAgent)
        && win.parent !== win);
    try { enabled = win.localStorage.getItem(storageKey) === 'true'; } catch (_) { /* private mode */ }

    function browserAvailable() {
        return win.parent === win && win.isSecureContext && typeof win.Notification === 'function';
    }

    function hostAlive() {
        return desktop && hostOrigin !== null && Date.now() - hostSeen < 10000;
    }

    function clientKind() {
        if (closed || notificationFailed) return null;
        if (desktop) return hostAlive() && hostSupported ? 'electron' : null;
        return enabled && browserAvailable() && win.Notification.permission === 'granted' ? 'browser' : null;
    }

    function sendHost(message) {
        if (!hostAlive()) return;
        // Production Electron loads its trusted renderer from file://. That
        // opaque origin cannot be a postMessage targetOrigin. Only this already
        // validated parent receives the wildcard reply; ordinary embedded pages
        // never pass desktop/source/origin checks and never receive any data.
        const target = hostOrigin === 'null' || hostOrigin === 'file://' ? '*' : hostOrigin;
        win.parent.postMessage(Object.assign({source: 'aes-runtime'}, message), target);
    }

    function trustedHostOrigin(origin) {
        if (origin === 'null' || origin === 'file://') return true;
        try {
            const url = new URL(origin);
            return (url.protocol === 'http:' || url.protocol === 'https:')
                && ['localhost', '127.0.0.1'].includes(url.hostname);
        } catch (_) { return false; }
    }

    function onHostMessage(event) {
        const data = event.data;
        if (!desktop || event.source !== win.parent || !trustedHostOrigin(event.origin)
            || !data || data.source !== 'aes-runtime-host' || data.type !== 'ready'
            || typeof data.notificationSupported !== 'boolean') return;
        if (hostOrigin !== null && hostOrigin !== event.origin) return;
        hostOrigin = event.origin;
        hostSeen = Date.now();
        hostSupported = data.notificationSupported;
        if (currentProgress && !closed) sendProgress(currentProgress);
        renderControls();
    }

    function notificationStatus() {
        if (closed) return text('连接已断开，恢复连接后继续接收通知。', 'Disconnected; notifications resume after reconnecting.');
        if (desktop) {
            if (!hostAlive()) return text('正在连接桌面通知。', 'Connecting to desktop notifications.');
            return hostSupported ? text('桌面系统通知已就绪。', 'Desktop system notifications are ready.')
                : text('当前系统不支持桌面通知。', 'Desktop notifications are unavailable on this system.');
        }
        if (win.parent !== win) return text('请在独立浏览器窗口中启用通知。', 'Open this page in its own browser window to enable notifications.');
        if (!win.isSecureContext) return text('浏览器通知需要安全连接或本机地址。', 'Browser notifications require a secure connection or localhost.');
        if (typeof win.Notification !== 'function') return text('此浏览器不支持系统通知。', 'This browser does not support system notifications.');
        if (notificationFailed) return text('系统通知发送失败，请检查浏览器设置后重新启用。', 'Notification delivery failed; check browser settings and enable it again.');
        if (win.Notification.permission === 'denied') return text('通知权限已被拒绝，请在浏览器网站设置中允许。', 'Notifications are blocked; allow them in browser site settings.');
        if (clientKind() === 'browser') return text('浏览器系统通知已启用，请保持网页打开。', 'Browser system notifications are enabled; keep this page open.');
        return text('点击启用后，浏览器才会请求通知权限。', 'Enable notifications to request browser permission.');
    }

    function renderControls() {
        doc.querySelectorAll('[data-aes-notification-control]').forEach(container => {
            let status = container.querySelector('[data-aes-notification-status]');
            if (!status) {
                status = doc.createElement('div');
                status.setAttribute('data-aes-notification-status', '');
                status.style.cssText = 'font-size:.85rem;margin:.5rem 0;opacity:.8';
                container.appendChild(status);
                if (!desktop) {
                    const button = doc.createElement('button');
                    button.setAttribute('type', 'button');
                    button.className = 'btn btn-secondary';
                    button.setAttribute('data-aes-notification-enable', '');
                    button.textContent = text('启用系统通知', 'Enable system notifications');
                    button.addEventListener('click', enableNotifications);
                    container.appendChild(button);
                }
            }
            status.textContent = notificationStatus();
            const button = container.querySelector('[data-aes-notification-enable]');
            if (button) button.disabled = closed || permissionPending || !browserAvailable()
                || win.Notification.permission === 'denied' || clientKind() === 'browser';
        });
    }

    async function enableNotifications() {
        if (desktop || closed || permissionPending || !browserAvailable()
            || win.Notification.permission === 'denied') return;
        permissionPending = true;
        try {
            // This function is attached directly to the DOM click handler. A
            // server-side button callback would lose the browser user gesture.
            const permission = win.Notification.permission === 'granted' ? 'granted'
                : await win.Notification.requestPermission();
            enabled = permission === 'granted';
            notificationFailed = false;
            try { win.localStorage.setItem(storageKey, String(enabled)); } catch (_) { /* private mode */ }
        } catch (_) {
            notificationFailed = true;
        } finally {
            permissionPending = false;
            renderControls();
        }
    }

    function sendProgress(progress) {
        sendHost({type: 'progress', progress: {
            config_name: progress.config_name, state: progress.state,
            completed: progress.completed, total: progress.total, task: progress.task,
        }});
    }

    function renderProgress(progress) {
        const active = progress.state === 'running';
        const known = Number.isFinite(progress.total) && progress.total > 0
            && Number.isFinite(progress.completed) && progress.completed >= 0;
        const completed = known ? Math.min(progress.completed, progress.total) : 0;
        const percent = known ? Math.floor(100 * completed / progress.total) : null;
        const running = text('运行中', 'Running');
        doc.title = active ? (known ? `[${percent}%] ` : `[${running}] `) + originalTitle : originalTitle;
        const parent = doc.getElementById('pywebio-scope-header_title');
        if (parent) {
            let status = doc.getElementById('aes-runtime-progress');
            if (!status) {
                status = doc.createElement('div');
                status.id = 'aes-runtime-progress';
                status.style.cssText = 'font-size:.75rem;text-align:center;opacity:.8;max-width:32rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
                status.setAttribute('role', 'status');
                parent.appendChild(status);
            }
            const stateText = {waiting: text('等待调度', 'Waiting'), error: text('执行中断', 'Interrupted'),
                completed: text('本轮执行结束', 'Current run finished')}[progress.state] || '';
            const count = known ? text(`已处理 ${completed}/${progress.total} 项任务`, `${completed}/${progress.total} tasks processed`) : running;
            status.textContent = active ? count + (progress.task_label ? ` · ${progress.task_label}` : '') : stateText;
            status.hidden = !status.textContent;
        }
    }

    function notify(notification) {
        if (!notification || !['string', 'number'].includes(typeof notification.id)
            || seenNotifications.has(notification.id)) return;
        const kind = clientKind();
        if (!kind) return;
        seenNotifications.add(notification.id);
        if (seenNotifications.size > 200) seenNotifications.delete(seenNotifications.values().next().value);
        if (kind === 'electron') {
            sendHost({type: 'notification', notification});
        } else {
            try {
                new win.Notification(notification.title || 'AES', {
                    body: notification.content || '', tag: `aes-${notification.id}`,
                });
            } catch (_) {
                notificationFailed = true;
                renderControls();
            }
        }
    }

    function update(progress, notifications) {
        if (closed) return;
        lastUpdate = Date.now();
        currentProgress = progress;
        renderProgress(progress);
        sendProgress(progress);
        (notifications || []).forEach(notify);
        renderControls();
    }

    function clearProgress() {
        currentProgress = {config_name: '', state: 'stopped', completed: 0, total: null, task: ''};
        renderProgress(currentProgress);
        sendProgress(currentProgress);
    }

    function stop() {
        if (closed) return;
        clearProgress();
        closed = true;
        win.clearInterval(watchdog);
        win.removeEventListener('message', onHostMessage);
        win.removeEventListener('pagehide', stop);
        renderControls();
    }

    win.addEventListener('message', onHostMessage);
    win.addEventListener('pagehide', stop);
    if (win.WebIO && win.WebIO._state && win.WebIO._state.CurrentSession) {
        win.WebIO._state.CurrentSession.on_session_close(stop);
    }
    const watchdog = win.setInterval(() => {
        if (Date.now() - lastUpdate > 15000) clearProgress();
        renderControls();
    }, 3000);
    renderControls();
    return {clientKind, update, stop, enableNotifications};
}));
