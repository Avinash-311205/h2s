import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Module 7: Policymaker Dashboard (React + Vite)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173, // default Vite dev port; override via CLI if needed
  },
});