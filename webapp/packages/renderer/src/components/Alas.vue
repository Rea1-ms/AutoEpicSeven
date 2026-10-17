<template>
  <iframe
    ref="webuiFrame"
    class="alas"
    :src="webuiUrl"
    @load="onFrameLoad"
    @error="clearProgress"
  ></iframe>
</template>

<script lang="ts">
import {computed, defineComponent, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {parseRuntimeFrameMessage, runtimeWebuiOrigin} from '@common/runtime';
import {useAppStoreWithOut} from '/@/store/modules/app';

export default defineComponent({
  name: 'AlasPage',
  setup() {
    const appStore = useAppStoreWithOut();
    const webuiUrl = computed(() => appStore.webuiUrl);
    const webuiFrame = ref<HTMLIFrameElement | null>(null);
    let handshakeTimer: ReturnType<typeof setInterval> | undefined;
    let mounted = false;
    let notificationSupported = false;
    const clearProgress = () => window.__electron_preload__runtimeClearProgress();

    function sendHandshake() {
      const origin = runtimeWebuiOrigin(webuiUrl.value);
      if (!mounted || !origin) return;
      webuiFrame.value?.contentWindow?.postMessage(
        {source: 'aes-runtime-host', type: 'ready', notificationSupported},
        origin,
      );
    }

    async function onFrameLoad() {
      clearProgress();
      try {
        const capabilities = await window.__electron_preload__runtimeCapabilities();
        notificationSupported = capabilities.notificationSupported === true;
      } catch (error) {
        notificationSupported = false;
        console.warn('Cannot query desktop notification support', error);
      }
      sendHandshake();
    }

    function onRuntimeMessage(event: MessageEvent) {
      const message = parseRuntimeFrameMessage(
        event,
        webuiFrame.value?.contentWindow,
        webuiUrl.value,
      );
      if (!message) return;
      if (message.type === 'progress') {
        window.__electron_preload__runtimeSetProgress(message.progress);
      } else {
        window
          .__electron_preload__runtimeNotify(message.notification)
          .then(sent => {
            if (!sent) {
              notificationSupported = false;
              sendHandshake();
              console.warn('Desktop notification delivery is unavailable');
            }
          })
          .catch(error => {
            notificationSupported = false;
            sendHandshake();
            console.warn('Cannot forward desktop notification', error);
          });
      }
    }

    watch(webuiUrl, clearProgress);
    onMounted(() => {
      mounted = true;
      window.addEventListener('message', onRuntimeMessage);
      window.addEventListener('beforeunload', clearProgress);
      // WebUI initialization may finish after iframe load, so the handshake must be repeatable.
      handshakeTimer = setInterval(sendHandshake, 1000);
    });
    onBeforeUnmount(() => {
      mounted = false;
      clearInterval(handshakeTimer);
      window.removeEventListener('message', onRuntimeMessage);
      window.removeEventListener('beforeunload', clearProgress);
      clearProgress();
    });
    return {webuiUrl, webuiFrame, onFrameLoad, clearProgress};
  },
});
</script>

<style scoped>
.alas {
  border-width: 0;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
}
</style>
