const useIpcRenderer = () => {
  return {
    ipcRendererSend: window.__electron_preload__ipcRendererSend,
    ipcRendererOn: window.__electron_preload__ipcRendererOn,
    ipcRendererInvoke: window.__electron_preload__ipcRendererInvoke,
  };
};

export default useIpcRenderer;
