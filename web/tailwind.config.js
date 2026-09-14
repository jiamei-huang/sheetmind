import {
  BRAND_BLUE_SCALE,
  ERROR_SCALE,
  SUCCESS_SCALE,
  WARNING_SCALE,
} from "./src/constants/colors.js";

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        blue: BRAND_BLUE_SCALE,
        emerald: SUCCESS_SCALE,
        green: SUCCESS_SCALE,
        amber: WARNING_SCALE,
        red: ERROR_SCALE,
      },
    },
  },
  plugins: [],
}
