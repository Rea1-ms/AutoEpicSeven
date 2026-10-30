import type {ShortcutDetails} from 'electron';
import type {Stats} from 'node:fs';
import type {PlatformPath} from 'node:path';

export type NotificationIdentityOptions = {
  platform: string;
  isPackaged: boolean;
  appDataPath: string;
  executablePath: string;
  appId: string;
};

export type NotificationIdentityDependencies = {
  path: Pick<PlatformPath, 'isAbsolute' | 'normalize' | 'join' | 'dirname'>;
  lstat: (path: string) => Pick<Stats, 'isDirectory' | 'isFile' | 'isSymbolicLink'>;
  mkdir: (path: string) => unknown;
  // Must reserve a new file exclusively, e.g. closeSync(openSync(path, 'wx')).
  // Electron's 'create' operation alone can overwrite an existing shortcut.
  reserveShortcut: (path: string) => void;
  readShortcut: (path: string) => ShortcutDetails;
  writeShortcut: (
    path: string,
    operation: 'create' | 'update',
    details: ShortcutDetails,
  ) => boolean;
};

function hasCode(error: unknown, code: string): boolean {
  return error !== null && typeof error === 'object' && 'code' in error && error.code === code;
}

export function createNotificationIdentity(
  options: NotificationIdentityOptions,
  deps: NotificationIdentityDependencies,
  warn: (message: string) => void,
) {
  // Evaluate once, lazily, when sending the first notification. Capability queries
  // must not create shortcuts. Cache failures too: a partial shortcut is retained
  // for inspection, never replaced or repeatedly rewritten by later notifications.
  let result: boolean | undefined;

  function inspect(path: string) {
    try {
      return deps.lstat(path);
    } catch (error) {
      if (hasCode(error, 'ENOENT')) return null;
      throw error;
    }
  }

  function directory(path: string, mayCreate: boolean) {
    let entry = inspect(path);
    if (!entry && mayCreate) {
      try {
        // Create one level only; every parent was checked before arriving here.
        deps.mkdir(path);
      } catch (error) {
        if (!hasCode(error, 'EEXIST')) throw error;
      }
      entry = inspect(path);
    }
    if (!entry || entry.isSymbolicLink() || !entry.isDirectory()) {
      throw new Error(`Notification shortcut directory is missing or unsafe: ${path}`);
    }
  }

  function prepare() {
    const {path} = deps;
    const {appDataPath, executablePath, appId} = options;
    if (
      !path.isAbsolute(appDataPath) ||
      !path.isAbsolute(executablePath) ||
      !/^[A-Za-z0-9][A-Za-z0-9.-]{0,126}[A-Za-z0-9]$/.test(appId)
    ) {
      throw new Error('Notification identity requires absolute paths and a valid application ID.');
    }

    const executable = path.normalize(executablePath);
    let parent = path.normalize(appDataPath);
    directory(parent, false);
    for (const name of [
      'Microsoft',
      'Windows',
      'Start Menu',
      'Programs',
      'AutoEpicSeven Notifications',
    ]) {
      parent = path.join(parent, name);
      directory(parent, true);
    }
    const shortcutPath = path.join(parent, `AutoEpicSeven (${appId}).lnk`);

    const shortcutDetails: ShortcutDetails = {
      target: executable,
      cwd: path.dirname(executable),
      appUserModelId: appId,
      description: 'AutoEpicSeven system notifications',
      icon: executable,
      iconIndex: 0,
      // Electron 22 binds live toast click callbacks directly. Do not invent a
      // toastActivatorClsid: durable Action Center activation needs a registered
      // COM activator, which this running-application notification path does not own.
    };

    function existingMatches(allowRelocation = false, requireMarker = false): boolean {
      const entry = inspect(shortcutPath);
      if (!entry || entry.isSymbolicLink() || !entry.isFile()) {
        warn(`Notification shortcut conflict; existing path was not changed: ${shortcutPath}`);
        return false;
      }
      const details = deps.readShortcut(shortcutPath);
      if (
        typeof details.target !== 'string' ||
        !path.isAbsolute(details.target) ||
        details.appUserModelId !== appId ||
        (requireMarker && details.description !== shortcutDetails.description)
      ) {
        warn(
          `Notification shortcut conflict; target or application identity does not match: ${shortcutPath}`,
        );
        return false;
      }
      if (path.normalize(details.target).toLowerCase() === executable.toLowerCase()) return true;
      if (!allowRelocation || details.description !== shortcutDetails.description) {
        warn(`Notification shortcut conflict; existing target was not changed: ${shortcutPath}`);
        return false;
      }
      // A portable installation may move. Only our own marked shortcut in the
      // dedicated directory may follow it; an app ID alone does not prove ownership.
      // Verify the result without permitting another update, so a failed native
      // write cannot recurse or repeatedly overwrite a conflicting shortcut.
      if (!deps.writeShortcut(shortcutPath, 'update', {...shortcutDetails, args: ''})) {
        throw new Error(`Cannot update notification shortcut after relocation: ${shortcutPath}`);
      }
      return existingMatches(false, true);
    }

    if (inspect(shortcutPath)) return existingMatches(true);
    try {
      deps.reserveShortcut(shortcutPath);
    } catch (error) {
      // Another process can create a shortcut between inspection and reservation.
      // A matching shortcut is reusable, but a conflicting one must stay untouched.
      if (hasCode(error, 'EEXIST')) return existingMatches(true);
      throw error;
    }
    const reserved = inspect(shortcutPath);
    if (!reserved || reserved.isSymbolicLink() || !reserved.isFile()) {
      throw new Error(`Notification shortcut reservation is unsafe: ${shortcutPath}`);
    }
    const written = deps.writeShortcut(shortcutPath, 'create', shortcutDetails);
    if (!written) {
      throw new Error(
        `Cannot write notification shortcut; reserved file was retained: ${shortcutPath}`,
      );
    }
    return existingMatches(false, true);
  }

  function ensureReady(): boolean {
    if (options.platform !== 'win32' || !options.isPackaged) return true;
    if (result !== undefined) return result;
    result = false;
    try {
      result = prepare();
    } catch (error) {
      warn(`Cannot prepare Windows notification identity: ${String(error)}`);
    }
    return result;
  }

  return {ensureReady};
}
