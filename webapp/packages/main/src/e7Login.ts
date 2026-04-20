import {app, BrowserWindow, session} from 'electron';
import type {Cookie} from 'electron';
import {ensureFile, pathExists, readJson, remove, writeJson} from 'fs-extra';
import {join} from 'node:path';
import logger from '/@/logger';

const PARTITION = 'persist:e7-login';
const LOGIN_URL = 'https://epic7-community.zlongame.com/';
const COOKIE_NAMESPACE = '1611630374326';
const AUTH_COOKIE = `_pd_auth_${COOKIE_NAMESPACE}`;
const DVID_COOKIE = `_pd_dvid_${COOKIE_NAMESPACE}`;
const ZLONGAME_URL_FILTER = {urls: ['https://*.zlongame.com/*']};

export interface E7Credentials {
  token: string;
  pd_did: string;
  pd_dvid: string;
  jsessionid?: string;
  expiry?: number;
  captured_at: number;
}

const credentialsPath = () => join(app.getPath('userData'), 'e7-credentials.json');

function decodeJwtExpiry(token: string): number | undefined {
  try {
    const parts = token.split('.');
    if (parts.length < 2) return undefined;
    const payload = parts[1];
    const padded = payload + '='.repeat((-payload.length) & 3);
    const json = Buffer.from(padded.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');
    const claims = JSON.parse(json);
    return typeof claims.exp === 'number' ? claims.exp : undefined;
  } catch {
    return undefined;
  }
}

let activeLoginWindow: BrowserWindow | null = null;

export async function openE7LoginWindow(): Promise<E7Credentials | null> {
  if (activeLoginWindow && !activeLoginWindow.isDestroyed()) {
    activeLoginWindow.focus();
    return null;
  }

  const loginSession = session.fromPartition(PARTITION);
  let capturedPdDid: string | undefined;

  loginSession.webRequest.onBeforeSendHeaders(ZLONGAME_URL_FILTER, (details, callback) => {
    const headers = details.requestHeaders ?? {};
    const val = headers['pd-did'] ?? headers['Pd-Did'] ?? headers['PD-DID'];
    if (typeof val === 'string' && val) {
      capturedPdDid = val;
    }
    callback({requestHeaders: headers});
  });

  const win = new BrowserWindow({
    width: 1100,
    height: 780,
    title: 'Epic Seven Community Login',
    webPreferences: {
      partition: PARTITION,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  activeLoginWindow = win;

  // Allow zlongame popups (e.g. captcha/sms iframes) to open in the same partition.
  win.webContents.setWindowOpenHandler(({url}) => {
    try {
      const {hostname} = new URL(url);
      if (hostname.endsWith('.zlongame.com')) {
        return {
          action: 'allow',
          overrideBrowserWindowOptions: {
            webPreferences: {
              partition: PARTITION,
              contextIsolation: true,
              nodeIntegration: false,
              sandbox: true,
            },
          },
        };
      }
    } catch {
      // fall through
    }
    return {action: 'deny'};
  });

  return new Promise<E7Credentials | null>(resolve => {
    let settled = false;

    const cleanup = () => {
      try {
        loginSession.webRequest.onBeforeSendHeaders(null);
      } catch (e) {
        logger.warn(`[e7-login] clear webRequest listener failed: ${e}`);
      }
      try {
        loginSession.cookies.removeListener('changed', onCookieChanged);
      } catch (e) {
        logger.warn(`[e7-login] remove cookies listener failed: ${e}`);
      }
    };

    const finish = (value: E7Credentials | null) => {
      if (settled) return;
      settled = true;
      cleanup();
      activeLoginWindow = null;
      if (!win.isDestroyed()) {
        win.close();
      }
      resolve(value);
    };

    const tryCapture = async () => {
      try {
        const cookies = await loginSession.cookies.get({});
        const auth = cookies.find(c => c.name === AUTH_COOKIE)?.value;
        const dvid = cookies.find(c => c.name === DVID_COOKIE)?.value;
        const jsession = cookies.find(c => c.name === 'JSESSIONID')?.value;
        if (!auth || !dvid || !capturedPdDid) return;

        const creds: E7Credentials = {
          token: auth,
          pd_did: capturedPdDid,
          pd_dvid: dvid,
          jsessionid: jsession,
          expiry: decodeJwtExpiry(auth),
          captured_at: Math.floor(Date.now() / 1000),
        };
        await persistE7Credentials(creds);
        logger.info(`[e7-login] captured credentials at ${credentialsPath()}`);
        finish(creds);
      } catch (e) {
        logger.error(`[e7-login] capture error: ${e}`);
      }
    };

    const onCookieChanged = (_event: unknown, cookie: Cookie) => {
      if (cookie.name === AUTH_COOKIE || cookie.name === DVID_COOKIE) {
        void tryCapture();
      }
    };
    loginSession.cookies.on('changed', onCookieChanged);

    win.on('closed', () => finish(null));

    win.loadURL(LOGIN_URL).catch(e => {
      logger.error(`[e7-login] loadURL failed: ${e}`);
      finish(null);
    });
  });
}

export async function persistE7Credentials(creds: E7Credentials): Promise<void> {
  const p = credentialsPath();
  await ensureFile(p);
  await writeJson(p, creds, {spaces: 2});
}

export async function getE7Credentials(): Promise<E7Credentials | null> {
  const p = credentialsPath();
  if (!(await pathExists(p))) return null;
  try {
    return (await readJson(p)) as E7Credentials;
  } catch (e) {
    logger.error(`[e7-login] read credentials failed: ${e}`);
    return null;
  }
}

export async function clearE7Credentials(): Promise<void> {
  const p = credentialsPath();
  if (await pathExists(p)) {
    await remove(p);
    logger.info(`[e7-login] cleared credentials at ${p}`);
  }
}

export function getE7CredentialsPath(): string {
  return credentialsPath();
}
