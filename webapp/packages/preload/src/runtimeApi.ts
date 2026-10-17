import {ipcRenderer} from 'electron';
import type {RuntimeCapabilities, RuntimeNotification, RuntimeProgress} from '@common/runtime';
import {
  RUNTIME_CAPABILITIES,
  RUNTIME_CLEAR,
  RUNTIME_NOTIFY,
  RUNTIME_PROGRESS,
} from '@common/runtime';

export function runtimeSetProgress(progress: RuntimeProgress): void {
  ipcRenderer.send(RUNTIME_PROGRESS, progress);
}

export function runtimeClearProgress(): void {
  ipcRenderer.send(RUNTIME_CLEAR);
}

export function runtimeNotify(notification: RuntimeNotification): Promise<boolean> {
  return ipcRenderer.invoke(RUNTIME_NOTIFY, notification);
}

export function runtimeCapabilities(): Promise<RuntimeCapabilities> {
  return ipcRenderer.invoke(RUNTIME_CAPABILITIES);
}
