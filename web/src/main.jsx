import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
import "@fontsource-variable/urbanist";
import "@fontsource/dm-mono/400.css";
import "@fontsource/dm-mono/500.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
