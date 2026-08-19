import { createApp } from "vue";
import App from "./App.vue";
import "./styles.css";
import { installDesktopBridge } from "./platform/tauri-bridge";

async function bootstrap(): Promise<void> {
  try {
    await installDesktopBridge();
  } catch (error) {
    console.error("Failed to install desktop bridge", error);
  }
  createApp(App).mount("#app");
}

void bootstrap();
