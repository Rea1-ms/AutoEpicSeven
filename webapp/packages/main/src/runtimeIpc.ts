import type {BrowserWindow, IpcMainEvent, IpcMainInvokeEvent} from 'electron';
import {app, ipcMain, Notification, shell} from 'electron';
import {closeSync, lstatSync, mkdirSync, openSync} from 'node:fs';
import {win32} from 'node:path';
import {
  DESKTOP_APP_ID,
  isRuntimeIpcSender,
  parseRuntimeNotification,
  parseRuntimeProgress,
  RUNTIME_CAPABILITIES,
  RUNTIME_CLEAR,
  RUNTIME_NOTIFY,
  RUNTIME_PROGRESS,
} from '@common/runtime';
import {createRuntimeController} from './runtimeController';
import {createNotificationIdentity} from './runtimeNotificationIdentity';
import logger from './logger';

export function installRuntimeIpc(mainWindow: BrowserWindow, rendererUrl: string) {
  const notificationIdentity = createNotificationIdentity(
    {
      platform: process.platform,
      isPackaged: app.isPackaged,
      appDataPath: app.getPath('appData'),
      executablePath: process.execPath,
      appId: DESKTOP_APP_ID,
    },
    {
      path: win32,
      lstat: lstatSync,
      mkdir: path => mkdirSync(path),
      reserveShortcut: path => closeSync(openSync(path, 'wx')),
      readShortcut: path => shell.readShortcutLink(path),
      writeShortcut: (path, operation, details) =>
        shell.writeShortcutLink(path, operation, details),
    },
    message => logger.warn(message),
  );
  const controller = createRuntimeController(
    mainWindow,
    {
      isSupported: () => Notification.isSupported(),
      ensureReady: notificationIdentity.ensureReady,
      create: notification =>
        new Notification({title: notification.title, body: notification.content}),
    },
    message => logger.warn(message),
  );
  const trusted = (event: IpcMainEvent | IpcMainInvokeEvent) =>
    !mainWindow.isDestroyed() && isRuntimeIpcSender(event, mainWindow.webContents, rendererUrl);
  const onProgress = (event: IpcMainEvent, payload: unknown) => {
    if (!trusted(event)) return;
    const progress = parseRuntimeProgress(payload);
    if (progress) controller.setProgress(progress);
  };
  const onClear = (event: IpcMainEvent) => {
    if (trusted(event)) controller.clearProgress();
  };
  ipcMain.on(RUNTIME_PROGRESS, onProgress);
  ipcMain.on(RUNTIME_CLEAR, onClear);
  ipcMain.handle(RUNTIME_NOTIFY, (event, payload: unknown) => {
    if (!trusted(event)) return false;
    const notification = parseRuntimeNotification(payload);
    return notification ? controller.notify(notification) : false;
  });
  ipcMain.handle(RUNTIME_CAPABILITIES, event => ({
    notificationSupported: trusted(event) && controller.notificationSupported(),
  }));

  // This watchdog lives in the main process, so a frozen/crashed renderer cannot leave a stale bar.
  const watchdog = setInterval(() => controller.expireProgress(), 5000);
  watchdog.unref();
  const clear = () => controller.clearProgress();
  mainWindow.webContents.on('did-start-navigation', clear);
  mainWindow.webContents.on('did-fail-load', clear);
  mainWindow.webContents.on('render-process-gone', clear);
  mainWindow.webContents.on('destroyed', clear);
  mainWindow.once('close', clear);
  mainWindow.once('closed', () => {
    clearInterval(watchdog);
    controller.dispose();
    ipcMain.removeListener(RUNTIME_PROGRESS, onProgress);
    ipcMain.removeListener(RUNTIME_CLEAR, onClear);
    ipcMain.removeHandler(RUNTIME_NOTIFY);
    ipcMain.removeHandler(RUNTIME_CAPABILITIES);
  });
}
