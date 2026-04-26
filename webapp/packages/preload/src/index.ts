/**
 * @module preload
 */

import {setupWebuiIpcBridge} from './webuiIpcBridge';

setupWebuiIpcBridge();

export {sha256sum} from './nodeCrypto';
export {versions} from './versions';
export {ipcRendererSend, ipcRendererOn, ipcRendererInvoke} from './electronApi';
export {getAlasConfig, checkIsNeedInstall, getAlasConfigDirFiles} from './alasConfig';
export {copyFilesToDir} from '@common/utils/copyFilesToDir';
export {modifyConfigYaml} from './modifyConfigYaml';
