import {ipcRenderer} from 'electron';

const WEBUI_IPC_REQUEST_SOURCE = 'aes-webui';
const WEBUI_IPC_RESPONSE_SOURCE = 'aes-electron-bridge';
const WEBUI_IPC_INVOKE = 'ipc-invoke';
const WEBUI_IPC_RESPONSE = 'ipc-response';
const ALLOWED_WEBUI_IPC_CHANNELS = new Set([
  'e7:open-login',
  'e7:close-login',
  'e7:get-credentials',
  'e7:clear-credentials',
  'e7:credentials-path',
]);

type WebuiIpcRequest = {
  source?: unknown;
  type?: unknown;
  requestId?: unknown;
  channel?: unknown;
  args?: unknown;
};

function postIpcResponse(event: MessageEvent, data: Record<string, unknown>): void {
  const source = event.source as WindowProxy | null;
  source?.postMessage(
    {
      source: WEBUI_IPC_RESPONSE_SOURCE,
      type: WEBUI_IPC_RESPONSE,
      ...data,
    },
    event.origin && event.origin !== 'null' ? event.origin : '*',
  );
}

export function setupWebuiIpcBridge(): void {
  window.addEventListener('message', async event => {
    const data = event.data as WebuiIpcRequest | null;
    if (!data || typeof data !== 'object') return;
    if (data.source !== WEBUI_IPC_REQUEST_SOURCE || data.type !== WEBUI_IPC_INVOKE) return;
    if (typeof data.requestId !== 'string' || typeof data.channel !== 'string') return;

    if (!ALLOWED_WEBUI_IPC_CHANNELS.has(data.channel)) {
      postIpcResponse(event, {
        requestId: data.requestId,
        ok: false,
        error: `Unsupported IPC channel: ${data.channel}`,
      });
      return;
    }

    try {
      const result = await ipcRenderer.invoke(data.channel, ...(Array.isArray(data.args) ? data.args : []));
      postIpcResponse(event, {
        requestId: data.requestId,
        ok: true,
        result,
      });
    } catch (error) {
      postIpcResponse(event, {
        requestId: data.requestId,
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  });
}
