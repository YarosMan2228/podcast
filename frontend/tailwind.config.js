import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        primary: '#4f46e5',   // indigo-600
        accent:  '#10b981',   // emerald-500
      },
    },
  },
  plugins: [typography],
}
