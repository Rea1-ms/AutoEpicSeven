import type {CoreService} from '/@/coreService';
import type {BrowserWindow} from 'electron';
import {app, ipcMain, nativeTheme} from 'electron';
import {
  E7_CLEAR_CREDENTIALS,
  E7_CLOSE_LOGIN,
  E7_CREDENTIALS_PATH,
  E7_GET_CREDENTIALS,
  E7_OPEN_LOGIN,
  ELECTRON_THEME,
  INSTALLER_READY,
  PAGE_ERROR,
  WINDOW_READY,
} from '@common/constant/eventNames';
import {ThemeObj} from '@common/constant/theme';
import logger from '/@/logger';
import {
  clearE7Credentials,
  closeE7LoginWindow,
  getE7Credentials,
  getE7CredentialsPath,
  openE7LoginWindow,
} from '/@/e7Login';

export const addIpcMainListener = async (mainWindow: BrowserWindow, coreService: CoreService) => {
  // Minimize, maximize, close window.
  ipcMain.on('window-tray', function () {
    mainWindow?.hide();
  });
  ipcMain.on('window-minimize', function () {
    mainWindow?.minimize();
  });
  ipcMain.on('window-maximize', function () {
    mainWindow?.isMaximized() ? mainWindow?.restore() : mainWindow?.maximize();
  });
  ipcMain.on('window-close', function () {
    coreService?.kill();
    mainWindow?.close();
    app.exit(0);
  });

  ipcMain.on(WINDOW_READY, async function (_, args) {
    logger.info('-----WINDOW_READY-----');
    args && (await coreService.run());
  });

  ipcMain.on(INSTALLER_READY, function () {
    logger.info('-----INSTALLER_READY-----');
    coreService.next();
  });

  ipcMain.on(ELECTRON_THEME, (_, args) => {
    logger.info('-----ELECTRON_THEME-----');
    nativeTheme.themeSource = ThemeObj[args];
  });

  ipcMain.on(PAGE_ERROR, (_, args) => {
    logger.info('-----PAGE_ERROR-----');
    logger.error(args);
  });

  // Fire-and-forget: open the login window and return immediately.
  // Credentials are persisted to disk asynchronously when captured;
  // awaiting the full login flow here would block the IPC bridge
  // and cause a timeout on the renderer/webui side.
  ipcMain.handle(E7_OPEN_LOGIN, (_, configName?: string) => {
    logger.info(`-----E7_OPEN_LOGIN----- config=${configName ?? '(default)'}`);
    openE7LoginWindow(configName).catch(e => {
      logger.error(`E7_OPEN_LOGIN background error: ${e}`);
    });
    return {opened: true};
  });

  ipcMain.handle(E7_CLOSE_LOGIN, () => {
    logger.info('-----E7_CLOSE_LOGIN-----');
    return closeE7LoginWindow();
  });

  ipcMain.handle(E7_GET_CREDENTIALS, async (_, configName?: string) => {
    return await getE7Credentials(configName);
  });

  ipcMain.handle(E7_CLEAR_CREDENTIALS, async (_, configName?: string) => {
    await clearE7Credentials(configName);
    return true;
  });

  ipcMain.handle(E7_CREDENTIALS_PATH, (_, configName?: string) => {
    return getE7CredentialsPath(configName);
  });
};
