/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        obsidian:  '#0D0D12',
        champagne: '#C9A84C',
        ivory:     '#FAF8F5',
        slate:     '#2A2A35',
      },
      fontFamily: {
        sans:  ['Inter', 'sans-serif'],
        drama: ['"Playfair Display"', 'serif'],
        mono:  ['"JetBrains Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
}

