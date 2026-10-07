import { createRoot } from "react-dom/client";
import { App } from "./app/App.tsx";
import { browserDependencies } from "./runs/browser.ts";
import { RunController } from "./runs/controller.ts";
import {
  HistoryController,
  IndexedDbHistoryStore,
  bindRunHistory,
} from "./history/index.ts";
import "./app/styles.css";

const controller = new RunController(browserDependencies());
const history = new HistoryController(
  new IndexedDbHistoryStore(),
  () => !controller.canLoadDraft(),
);
const unbindHistory = bindRunHistory(controller, history);
void history.initialize();
const root = createRoot(document.getElementById("root")!);
root.render(<App controller={controller} history={history} />);
window.addEventListener(
  "pagehide",
  () => {
    controller.dispose();
    unbindHistory();
    history.dispose();
  },
  { once: true },
);
