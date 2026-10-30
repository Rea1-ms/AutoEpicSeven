import type {RuntimeNotification, RuntimeProgress} from '../../common/runtime';
import type {BrowserWindow, Notification} from 'electron';

type RuntimeWindow = Pick<
  BrowserWindow,
  'isDestroyed' | 'setProgressBar' | 'isMinimized' | 'restore' | 'show' | 'focus'
>;
type NativeNotification = Pick<Notification, 'once' | 'show' | 'close'>;
type NotificationProvider = {
  isSupported: () => boolean;
  ensureReady?: () => boolean;
  create: (notification: RuntimeNotification) => NativeNotification;
};

export function createRuntimeController(
  mainWindow: RuntimeWindow,
  provider: NotificationProvider,
  warn: (message: string) => void,
) {
  const seenNotifications = new Set<string>();
  const activeNotifications = new Set<NativeNotification>();
  let disposed = false;
  let progressSignature = '';
  let lastProgressAt: number | null = null;

  const available = () => !disposed && !mainWindow.isDestroyed();

  function clearProgress() {
    lastProgressAt = null;
    if (!available() || progressSignature === '') return;
    mainWindow.setProgressBar(-1);
    progressSignature = '';
  }

  function setProgress(progress: RuntimeProgress, now = Date.now()) {
    if (!available()) return false;
    if (['waiting', 'stopped', 'completed'].includes(progress.state)) {
      clearProgress();
      return true;
    }
    const fraction = progress.total ? progress.completed / progress.total : 0;
    const mode = progress.state === 'error' ? 'error' : progress.total ? 'normal' : 'indeterminate';
    const value = mode === 'indeterminate' ? 2 : mode === 'error' && fraction === 0 ? 1 : fraction;
    const signature = `${value}:${mode}`;
    // Repeated reports are heartbeats. Refresh expiry even when the native bar is unchanged.
    lastProgressAt = now;
    if (progressSignature !== signature) {
      mainWindow.setProgressBar(value, {mode});
      progressSignature = signature;
    }
    return true;
  }

  function expireProgress(now = Date.now()) {
    if (lastProgressAt !== null && now - lastProgressAt >= 15000) clearProgress();
  }

  function notificationSupported() {
    if (!available()) return false;
    try {
      return provider.isSupported();
    } catch (error) {
      warn(`Cannot check system notification support: ${String(error)}`);
      return false;
    }
  }

  function notify(notification: RuntimeNotification) {
    if (!available()) return false;
    if (seenNotifications.has(notification.id)) return true;
    if (!notificationSupported()) {
      warn('System notifications are not supported on this desktop.');
      return false;
    }
    let nativeNotification: NativeNotification | undefined;
    try {
      if (provider.ensureReady && !provider.ensureReady()) return false;
      nativeNotification = provider.create(notification);
      const currentNotification = nativeNotification;
      activeNotifications.add(currentNotification);
      // Keep ownership until close/failure: dropping the last reference can lose native events.
      currentNotification.once('close', () => activeNotifications.delete(currentNotification));
      currentNotification.once('failed', (_event, error) => {
        activeNotifications.delete(currentNotification);
        warn(`System notification failed: ${String(error)}`);
      });
      currentNotification.once('click', () => {
        if (!available()) return;
        if (mainWindow.isMinimized()) mainWindow.restore();
        mainWindow.show();
        mainWindow.focus();
      });
      currentNotification.show();
      seenNotifications.add(notification.id);
      if (seenNotifications.size > 256) {
        const oldest = seenNotifications.values().next().value;
        if (oldest !== undefined) seenNotifications.delete(oldest);
      }
      if (activeNotifications.size > 32) {
        const oldest = activeNotifications.values().next().value;
        oldest?.close();
        if (oldest) activeNotifications.delete(oldest);
      }
      return true;
    } catch (error) {
      if (nativeNotification) activeNotifications.delete(nativeNotification);
      warn(`Cannot send system notification: ${String(error)}`);
      return false;
    }
  }

  function dispose() {
    if (disposed) return;
    clearProgress();
    disposed = true;
    for (const notification of activeNotifications) {
      try {
        notification.close();
      } catch (error) {
        warn(`Cannot close system notification: ${String(error)}`);
      }
    }
    activeNotifications.clear();
    seenNotifications.clear();
  }

  return {setProgress, clearProgress, expireProgress, notificationSupported, notify, dispose};
}
