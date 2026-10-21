export const RUNTIME_PROGRESS = 'runtime:progress';
export const RUNTIME_CLEAR = 'runtime:clear';
export const RUNTIME_NOTIFY = 'runtime:notify';
export const RUNTIME_CAPABILITIES = 'runtime:capabilities';
export const DESKTOP_APP_ID = 'com.electron.aes';

export type RuntimeProgress = {
  config_name: string;
  state: 'running' | 'waiting' | 'error' | 'stopped' | 'completed';
  completed: number;
  total: number | null;
  task: string;
};

export type RuntimeNotification = {id: string; title: string; content: string};
export type RuntimeCapabilities = {notificationSupported: boolean};
export type RuntimeMessage =
  | {source: 'aes-runtime'; type: 'progress'; progress: RuntimeProgress}
  | {source: 'aes-runtime'; type: 'notification'; notification: RuntimeNotification};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isText(value: unknown, maximum: number, allowEmpty = false): value is string {
  return (
    typeof value === 'string' && value.length <= maximum && (allowEmpty || value.trim() !== '')
  );
}

export function parseRuntimeProgress(value: unknown): RuntimeProgress | null {
  if (!isRecord(value)) return null;
  const {config_name, state, completed, total, task} = value;
  if (!isText(config_name, 128, true) || !isText(task, 256, true)) return null;
  if (
    typeof state !== 'string' ||
    !['running', 'waiting', 'error', 'stopped', 'completed'].includes(state)
  )
    return null;
  if (typeof completed !== 'number' || !Number.isSafeInteger(completed) || completed < 0)
    return null;
  if (total !== null && (typeof total !== 'number' || !Number.isSafeInteger(total) || total < 0)) {
    return null;
  }
  if (typeof total === 'number' && total > 0 && completed > total) return null;
  return {config_name, state: state as RuntimeProgress['state'], completed, total, task};
}

export function parseRuntimeNotification(value: unknown): RuntimeNotification | null {
  if (!isRecord(value)) return null;
  const {id, title, content} = value;
  const notificationId =
    typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? String(id) : id;
  if (!isText(notificationId, 128) || !isText(title, 256) || !isText(content, 4096, true))
    return null;
  return {id: notificationId, title, content};
}

export function runtimeWebuiOrigin(webuiUrl: string): string | null {
  try {
    const url = new URL(webuiUrl);
    return ['http:', 'https:'].includes(url.protocol) ? url.origin : null;
  } catch {
    return null;
  }
}

export function parseRuntimeFrameMessage(
  event: {source: unknown; origin: string; data: unknown},
  frameWindow: unknown,
  webuiUrl: string,
): RuntimeMessage | null {
  const origin = runtimeWebuiOrigin(webuiUrl);
  if (!frameWindow || event.source !== frameWindow || !origin || event.origin !== origin)
    return null;
  if (!isRecord(event.data) || event.data.source !== 'aes-runtime') return null;
  if (event.data.type === 'progress') {
    const progress = parseRuntimeProgress(event.data.progress);
    return progress ? {source: 'aes-runtime', type: 'progress', progress} : null;
  }
  if (event.data.type === 'notification') {
    const notification = parseRuntimeNotification(event.data.notification);
    return notification ? {source: 'aes-runtime', type: 'notification', notification} : null;
  }
  return null;
}

export function isRuntimeIpcSender(
  event: {sender: unknown; senderFrame: {url: string} | null},
  webContents: {mainFrame: {url: string}; isDestroyed: () => boolean},
  rendererUrl: string,
): boolean {
  if (webContents.isDestroyed() || event.sender !== webContents) return false;
  if (!event.senderFrame || event.senderFrame !== webContents.mainFrame) return false;
  try {
    const actual = new URL(event.senderFrame.url);
    const expected = new URL(rendererUrl);
    // Hash routing changes the current view but must never grant another document access.
    actual.hash = '';
    expected.hash = '';
    return actual.href === expected.href;
  } catch {
    return false;
  }
}
