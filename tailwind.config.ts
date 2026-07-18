import type { Config } from 'tailwindcss';

const config: Config = {
  // Files Tailwind scans to decide which utility classes to generate.
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Government-portal brand blues, available as e.g. bg-brand, text-brand-dark
        brand: {
          DEFAULT: '#1f7de2',
          dark: '#0f5fb4',
          deep: '#0f4c92',
        },
      },
    },
  },
  // Preflight (Tailwind's base reset) is enabled. Our own base styles in
  // globals.css are in @layer base *after* @tailwind base, so they layer on
  // top of the reset rather than being clobbered by it.
  plugins: [],
};

export default config;
