/** Tailwind config for strix-dash.
 *
 * Breakpoints extend well past Tailwind's defaults because the target display
 * is an Odyssey G9 at 5120x1440. The common failure on a screen that wide is a
 * centred max-w-7xl column leaving 60% of the panel black, so layouts here use
 * auto-fit grids with no max-width container.
 */
module.exports = {
  content: [
    "./frontend/src/index.html",
    "./frontend/src/js/**/*.js",
    "./frontend/dist/index.html",
    "./frontend/dist/js/**/*.js",
  ],
  darkMode: "class",
  theme: {
    extend: {
      screens: {
        "3xl": "2560px",
        "4xl": "3840px",
        "5xl": "5120px",
      },
      colors: {
        // Near-black with a slight cool bias, not pure #000.
        ink: {
          50: "#f6f7f9",
          100: "#e8eaee",
          200: "#c9ced8",
          300: "#a3abba",
          400: "#7c8697",
          500: "#5d6675",
          600: "#454d5a",
          700: "#333a45",
          800: "#22272f",
          900: "#161a20",
          950: "#0d1014",
        },
        accent: {
          300: "#f0a58c",
          500: "#e0552c",
          600: "#c4441f",
        },
        ok:   { 300: "#5fd3a3", 500: "#20a56f" },
        warn: { 300: "#e8bd6a", 500: "#c08a1c" },
        crit: { 300: "#f08a9c", 500: "#d63b57" },
      },
      fontFamily: {
        mono: [
          "ui-monospace", "SF Mono", "Cascadia Mono", "Menlo",
          "Consolas", "Liberation Mono", "monospace",
        ],
      },
    },
  },
  plugins: [],
};
