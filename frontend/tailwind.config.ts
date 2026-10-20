import type { Config } from "tailwindcss";

// Tailwind v4 reads the theme from src/app/globals.css; this file is not loaded
// (there is no @config). Its scale is kept equal to the CSS one.
const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          50: "#f3f6ff",
          100: "#e8eeff",
          200: "#cdd9ff",
          300: "#a8bfff",
          400: "#6f97ff",
          500: "#3d74ff",
          600: "#1a5cff",
          700: "#1550e8",
          800: "#1342bd",
          900: "#133894",
          950: "#0e2360",
        },
      },
    },
  },
  plugins: [],
};
export default config;
